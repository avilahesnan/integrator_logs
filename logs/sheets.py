"""Integração com o Google Sheets via API oficial.

O Sheets cumpre duas finalidades:

1. Preservar os cadastros das farmácias (aba de cadastros). Como o disco do
   Render é efêmero, o Sheets é a fonte de verdade do cadastro: o SQLite é
   repovoado a partir dele.
2. Manter uma cópia dos dados consolidados de monitoramento (aba de
   monitoramento), atualizada uma vez ao fim do dia.

Em ambas as abas a gravação é um *upsert* pelo ``store_id``: uma linha por
farmácia, nunca uma linha por requisição.

As credenciais nunca ficam no código-fonte. Elas saem de uma destas duas
origens, nesta ordem de precedência:

1. ``GOOGLE_SERVICE_ACCOUNT_JSON`` — o JSON inteiro da conta de serviço, como
   texto, numa variável de ambiente.
2. ``GOOGLE_SERVICE_ACCOUNT_FILE`` — o caminho de um arquivo com esse JSON. O
   padrão é ``/etc/secrets/google-credentials.json``, que é onde o Render
   monta um *Secret File* chamado ``google-credentials.json``.

A primeira vence quando as duas existem, para dar como sobrepor o arquivo sem
precisar removê-lo.
"""

import json
import logging
import os
import threading

from django.utils import timezone

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# O Render monta cada Secret File em /etc/secrets/<nome do arquivo>. Esse é o
# caminho procurado quando GOOGLE_SERVICE_ACCOUNT_JSON não está definida.
DEFAULT_SERVICE_ACCOUNT_FILE = '/etc/secrets/google-credentials.json'

CADASTROS_HEADER = [
    'store_id',
    'pharmacy',
    'city',
    'system',
    'type_access',
    'version',
    'updated_at',
]

MONITORAMENTO_HEADER = [
    'store_id',
    'pharmacy',
    'city',
    'system',
    'type_access',
    'version',
    'item_quantity',
    'last_sync',
    'last_request',
    'requests_today',
    'updated_at',
]

_client_lock = threading.Lock()
_client = None


class SheetsNotConfigured(Exception):
    """A integração com o Sheets não está configurada por variáveis de ambiente."""


class SheetsError(Exception):
    """Falha ao conversar com o Google Sheets."""


def _env(name, default=''):
    return (os.getenv(name) or default).strip()


def spreadsheet_id():
    return _env('GOOGLE_SHEETS_ID')


def cadastros_tab():
    return _env('SHEETS_CADASTROS_TAB', 'Cadastros')


def monitoramento_tab():
    return _env('SHEETS_MONITORAMENTO_TAB', 'Monitoramento')


def service_account_file():
    """Caminho do arquivo de credenciais. Por padrão, o Secret File do Render."""
    return _env('GOOGLE_SERVICE_ACCOUNT_FILE', DEFAULT_SERVICE_ACCOUNT_FILE)


def credential_source():
    """De onde a credencial vai sair, ou ``None`` se não houver nenhuma.

    Devolve o nome da variável de ambiente ou o caminho do arquivo. Não lê o
    arquivo nem valida o conteúdo: só confere se existe, porque isto roda no
    caminho de cada requisição recebida.
    """
    if _env('GOOGLE_SERVICE_ACCOUNT_JSON'):
        return 'GOOGLE_SERVICE_ACCOUNT_JSON'

    path = service_account_file()

    if path and os.path.isfile(path):
        return path

    return None


def _read_service_account():
    """Devolve ``(json_bruto, origem)``, lendo o arquivo se for o caso."""
    inline = _env('GOOGLE_SERVICE_ACCOUNT_JSON')

    if inline:
        return inline, 'a variável GOOGLE_SERVICE_ACCOUNT_JSON'

    path = service_account_file()

    try:
        with open(path, encoding='utf-8') as handle:
            content = handle.read().strip()
    except OSError as err:
        raise SheetsError(
            f'Não foi possível ler o arquivo de credenciais {path}: {err}. '
            'No Render, confirme o nome do Secret File — ele é montado em '
            '/etc/secrets/<nome do arquivo>.'
        ) from err

    if not content:
        raise SheetsError(f'O arquivo de credenciais {path} está vazio.')

    return content, f'o arquivo {path}'


def is_enabled():
    """A sincronização está ligada e configurada?"""
    if _env('SHEETS_SYNC_ENABLED', 'True').lower() in ('false', '0', 'no'):
        return False

    return bool(spreadsheet_id()) and bool(credential_source())


def configuration_error():
    """Mensagem explicando o que falta para configurar, ou ``None`` se está ok."""
    if _env('SHEETS_SYNC_ENABLED', 'True').lower() in ('false', '0', 'no'):
        return 'Sincronização desativada (SHEETS_SYNC_ENABLED=False).'

    missing = []

    if not spreadsheet_id():
        missing.append('GOOGLE_SHEETS_ID')

    if not credential_source():
        missing.append(
            'as credenciais da conta de serviço — defina '
            'GOOGLE_SERVICE_ACCOUNT_JSON ou disponibilize o arquivo '
            f'{service_account_file()}'
        )

    if missing:
        return f'Falta configurar: {"; ".join(missing)}.'

    return None


def _get_client():
    """Cliente gspread autenticado, criado uma vez e reaproveitado."""
    global _client

    if not is_enabled():
        raise SheetsNotConfigured(configuration_error())

    with _client_lock:
        if _client is not None:
            return _client

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as err:
            raise SheetsError(
                'Dependências do Google Sheets ausentes. '
                'Instale com: pip install gspread google-auth'
            ) from err

        raw, origem = _read_service_account()

        try:
            info = json.loads(raw)
        except json.JSONDecodeError as err:
            raise SheetsError(
                f'As credenciais em {origem} não formam um JSON válido. '
                'Use o conteúdo completo do arquivo da conta de serviço, '
                'sem cortar nada.'
            ) from err

        # A chave privada costuma chegar com \n escapado quando copiada para
        # uma variável de ambiente.
        if isinstance(info.get('private_key'), str):
            info['private_key'] = info['private_key'].replace('\\n', '\n')

        try:
            credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
            _client = gspread.authorize(credentials)
        except Exception as err:
            raise SheetsError(f'Falha ao autenticar no Google Sheets: {err}') from err

        return _client


def _get_worksheet(title, header):
    """Devolve a aba pedida, criando-a com cabeçalho se ainda não existir."""
    client = _get_client()

    try:
        spreadsheet = client.open_by_key(spreadsheet_id())
    except Exception as err:
        raise SheetsError(
            f'Não foi possível abrir a planilha {spreadsheet_id()}: {err}. '
            'Confirme o GOOGLE_SHEETS_ID e se a planilha foi compartilhada '
            'com o e-mail da conta de serviço.'
        ) from err

    try:
        worksheet = spreadsheet.worksheet(title)
    except Exception:
        try:
            worksheet = spreadsheet.add_worksheet(
                title=title,
                rows=200,
                cols=max(len(header), 12),
            )
            worksheet.update(range_name='A1', values=[header])
        except Exception as err:
            raise SheetsError(f'Não foi possível criar a aba "{title}": {err}') from err

        return worksheet

    # Garante o cabeçalho numa aba que exista mas esteja vazia.
    try:
        first_row = worksheet.row_values(1)
    except Exception:
        first_row = []

    if not first_row:
        worksheet.update(range_name='A1', values=[header])

    return worksheet


def _column_letter(index):
    """1 -> A, 27 -> AA."""
    letters = ''

    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters

    return letters


def _format_datetime(value):
    if not value:
        return ''

    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M:%S')


def _upsert_rows(worksheet, header, rows):
    """Atualiza as linhas existentes e acrescenta as novas, casando por ``store_id``.

    ``rows`` é uma lista de dicionários. Devolve ``(atualizadas, inseridas)``.
    """
    try:
        existing = worksheet.get_all_values()
    except Exception as err:
        raise SheetsError(f'Não foi possível ler a aba "{worksheet.title}": {err}') from err

    if not existing:
        existing = [header]
        worksheet.update(range_name='A1', values=[header])

    sheet_header = existing[0]

    # Usa o cabeçalho que já está na planilha quando ele cobre nossas colunas,
    # para respeitar a ordem/colunas extras que você tenha criado à mão.
    if all(column in sheet_header for column in header):
        active_header = sheet_header
    else:
        active_header = header
        worksheet.update(range_name='A1', values=[header])
        existing = [header] + existing[1:]

    try:
        store_id_column = active_header.index('store_id')
    except ValueError as err:
        raise SheetsError(
            f'A aba "{worksheet.title}" não tem a coluna "store_id" no cabeçalho.'
        ) from err

    # store_id -> número da linha na planilha (1-based, cabeçalho é a linha 1)
    row_by_store = {}

    for position, row in enumerate(existing[1:], start=2):
        if len(row) > store_id_column:
            key = (row[store_id_column] or '').strip()

            if key and key not in row_by_store:
                row_by_store[key] = position

    last_column = _column_letter(len(active_header))

    updates = []
    appends = []

    width = len(active_header)

    for record in rows:
        store_id = str(record.get('store_id', '')).strip()

        if not store_id:
            continue

        row_number = row_by_store.get(store_id)

        if row_number:
            # Parte da linha que já está na planilha, para só reescrever as
            # colunas que são nossas. Colunas que você acrescentou à mão
            # ficam intactas.
            current = list(existing[row_number - 1]) if len(existing) >= row_number else []
            values = (current + [''] * width)[:width]
        else:
            values = [''] * width

        for position, column in enumerate(active_header):
            if column in header:
                values[position] = record.get(column, '')

        if row_number:
            updates.append({
                'range': f'A{row_number}:{last_column}{row_number}',
                'values': [values],
            })
        else:
            appends.append(values)

    try:
        if updates:
            worksheet.batch_update(updates)

        if appends:
            worksheet.append_rows(appends, value_input_option='USER_ENTERED')
    except Exception as err:
        raise SheetsError(f'Falha ao gravar na aba "{worksheet.title}": {err}') from err

    return len(updates), len(appends)


def push_pharmacies(pharmacies):
    """Envia cadastros para a aba de cadastros. ``pharmacies`` é iterável de ``Pharmacy``."""
    rows = [
        {
            'store_id': item.store_id,
            'pharmacy': item.pharmacy,
            'city': item.city,
            'system': item.system,
            'type_access': item.type_access,
            'version': item.version,
            'updated_at': _format_datetime(item.updated_at or timezone.now()),
        }
        for item in pharmacies
    ]

    if not rows:
        return 0, 0

    worksheet = _get_worksheet(cadastros_tab(), CADASTROS_HEADER)

    return _upsert_rows(worksheet, CADASTROS_HEADER, rows)


def fetch_pharmacies():
    """Lê os cadastros da planilha e devolve uma lista de dicionários.

    Linhas sem ``store_id`` são ignoradas. Em caso de ``store_id`` repetido na
    planilha, a última linha vence.
    """
    worksheet = _get_worksheet(cadastros_tab(), CADASTROS_HEADER)

    try:
        records = worksheet.get_all_records()
    except Exception as err:
        raise SheetsError(f'Não foi possível ler os cadastros: {err}') from err

    cleaned = {}

    for record in records:
        store_id = str(record.get('store_id', '')).strip()

        if not store_id:
            continue

        cleaned[store_id] = {
            'store_id': store_id,
            'pharmacy': str(record.get('pharmacy', '')).strip(),
            'city': str(record.get('city', '')).strip(),
            'system': str(record.get('system', '')).strip(),
            'type_access': str(record.get('type_access', '')).strip().upper(),
            'version': str(record.get('version', '')).strip(),
        }

    return list(cleaned.values())


def push_monitoring(rows):
    """Envia o consolidado de monitoramento, uma linha por farmácia."""
    if not rows:
        return 0, 0

    worksheet = _get_worksheet(monitoramento_tab(), MONITORAMENTO_HEADER)
    now = _format_datetime(timezone.now())

    payload = [
        {
            'store_id': row.get('store_id', ''),
            'pharmacy': row.get('pharmacy', ''),
            'city': row.get('city', ''),
            'system': row.get('system', ''),
            'type_access': row.get('type_access', ''),
            'version': row.get('version', ''),
            'item_quantity': row.get('item_quantity') if row.get('item_quantity') is not None else '',
            'last_sync': _format_datetime(row.get('last_sync')),
            'last_request': _format_datetime(row.get('last_request')),
            'requests_today': row.get('requests_today', 0),
            'updated_at': now,
        }
        for row in rows
    ]

    return _upsert_rows(worksheet, MONITORAMENTO_HEADER, payload)
