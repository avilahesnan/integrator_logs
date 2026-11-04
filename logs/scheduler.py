"""Agendamento por carona nas requisições que o serviço já recebe.

O plano gratuito do Render não oferece Cron Job, e não queremos um serviço
pago. Como as farmácias batem no endpoint a cada ~20 minutos, cada requisição
recebida serve de gatilho: verificamos se a tarefa do dia já rodou e, se não,
executamos em uma thread separada para não atrasar a resposta do endpoint.

Duas tarefas:

* importação de cadastros — uma vez por dia, e também sempre que a tabela de
  farmácias estiver vazia (sinal de container novo, já que o disco do Render é
  efêmero);
* exportação do consolidado — uma vez por dia, a partir do horário definido em
  ``SHEETS_EXPORT_HOUR``.

Nenhuma falha aqui pode derrubar o endpoint: tudo é capturado e registrado.
"""

import logging
import os
import threading
from datetime import timedelta

from django.db import connections
from django.db.models import Q
from django.utils import timezone

from . import sheets
from .models import Pharmacy, SyncState
from .monitoring import build_overview

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_HOUR = 23

# Evita repetir a importação forçada em sequência quando a planilha está
# legitimamente vazia.
FORCED_IMPORT_COOLDOWN = timedelta(minutes=10)

_lock = threading.Lock()
_running = set()
_last_forced_import = None


def export_hour():
    try:
        value = int(os.getenv('SHEETS_EXPORT_HOUR', '') or DEFAULT_EXPORT_HOUR)
    except (TypeError, ValueError):
        return DEFAULT_EXPORT_HOUR

    return value if 0 <= value <= 23 else DEFAULT_EXPORT_HOUR


def _state(task):
    state, _ = SyncState.objects.get_or_create(task=task)

    return state


def _claim(task, today):
    """Marca a tarefa como feita hoje, de forma atômica.

    Devolve ``True`` apenas para quem conseguiu marcar, de modo que dois
    workers do gunicorn não executem a mesma tarefa em paralelo.
    """
    _state(task)

    claimed = SyncState.objects.filter(
        Q(task=task) & (Q(last_run_date__isnull=True) | Q(last_run_date__lt=today))
    ).update(last_run_date=today, last_run_at=timezone.now())

    return claimed == 1


def _finish(task, status, message):
    SyncState.objects.filter(task=task).update(
        last_run_at=timezone.now(),
        last_status=status,
        last_message=(message or '')[:2000],
    )


def _release(task):
    """Desmarca a data para que a tarefa possa ser tentada de novo hoje."""
    SyncState.objects.filter(task=task).update(last_run_date=None)


def import_pharmacies():
    """Lê os cadastros do Sheets e atualiza o SQLite, sem duplicar ``store_id``.

    Registros com envio pendente ao Sheets são preservados: eles têm alteração
    local mais nova que a planilha e não podem ser sobrescritos.
    """
    records = sheets.fetch_pharmacies()

    created = 0
    updated = 0
    skipped = 0

    for record in records:
        store_id = record['store_id']
        existing = Pharmacy.objects.filter(store_id=store_id).first()

        if existing is not None and not existing.sheets_synced:
            # Alteração local ainda não espelhada: a planilha está defasada.
            skipped += 1
            continue

        defaults = {
            'pharmacy': record['pharmacy'],
            'city': record['city'],
            'system': record['system'],
            'type_access': record['type_access'] or Pharmacy.ACCESS_DB,
            'version': record['version'],
            'sheets_synced': True,
            'sheets_synced_at': timezone.now(),
            'sheets_error': '',
        }

        if existing is None:
            Pharmacy.objects.create(store_id=store_id, **defaults)
            created += 1
        else:
            for field, value in defaults.items():
                setattr(existing, field, value)

            existing.save()
            updated += 1

    message = (
        f'{len(records)} cadastro(s) lido(s) do Sheets: '
        f'{created} criado(s), {updated} atualizado(s), '
        f'{skipped} preservado(s) por alteração local pendente.'
    )
    logger.info(message)

    return message


def export_monitoring():
    """Envia o consolidado do dia, uma linha por farmácia."""
    rows = build_overview()

    if not rows:
        return 'Nada a exportar: nenhuma farmácia cadastrada ou com requisições.'

    updated, appended = sheets.push_monitoring(rows)

    message = (
        f'{len(rows)} farmácia(s) exportada(s) para o monitoramento: '
        f'{updated} linha(s) atualizada(s), {appended} inserida(s).'
    )
    logger.info(message)

    return message


def _run(task, action):
    """Executa a tarefa registrando o resultado, sem nunca propagar exceção."""
    with _lock:
        if task in _running:
            return

        _running.add(task)

    try:
        message = action()
        _finish(task, SyncState.STATUS_OK, message)
    except sheets.SheetsNotConfigured as err:
        _finish(task, SyncState.STATUS_ERROR, str(err))
        logger.warning('%s não executada: %s', task, err)
    except Exception as err:
        _finish(task, SyncState.STATUS_ERROR, str(err))
        # Libera a trava do dia para que a próxima requisição tente de novo.
        _release(task)
        logger.exception('Falha em %s: %s', task, err)
    finally:
        with _lock:
            _running.discard(task)

        connections.close_all()


def _spawn(task, action):
    thread = threading.Thread(
        target=_run,
        args=(task, action),
        name=f'sync-{task}',
        daemon=True,
    )
    thread.start()


def run_import_now():
    """Importação manual, síncrona. Levanta exceção em caso de falha."""
    today = timezone.localdate()
    message = import_pharmacies()
    SyncState.objects.filter(task=SyncState.TASK_IMPORT).update(last_run_date=today)
    _finish(SyncState.TASK_IMPORT, SyncState.STATUS_OK, message)

    return message


def run_export_now():
    """Exportação manual, síncrona. Levanta exceção em caso de falha."""
    today = timezone.localdate()
    message = export_monitoring()
    SyncState.objects.filter(task=SyncState.TASK_EXPORT).update(last_run_date=today)
    _finish(SyncState.TASK_EXPORT, SyncState.STATUS_OK, message)

    return message


def ensure_daily_tasks():
    """Gatilho barato chamado pelas views. Nunca levanta exceção."""
    global _last_forced_import

    if not sheets.is_enabled():
        return

    try:
        now = timezone.localtime()
        today = now.date()

        # Importação: uma vez por dia, ou imediatamente se a tabela está vazia
        # (container novo no Render, disco efêmero zerado).
        empty = not Pharmacy.objects.exists()
        cooled_down = (
            _last_forced_import is None
            or timezone.now() - _last_forced_import > FORCED_IMPORT_COOLDOWN
        )

        if empty and cooled_down:
            _last_forced_import = timezone.now()
            _state(SyncState.TASK_IMPORT)
            _spawn(SyncState.TASK_IMPORT, import_pharmacies)
        elif _claim(SyncState.TASK_IMPORT, today):
            _spawn(SyncState.TASK_IMPORT, import_pharmacies)

        # Exportação: uma vez por dia, do horário configurado em diante.
        if now.hour >= export_hour() and _claim(SyncState.TASK_EXPORT, today):
            _spawn(SyncState.TASK_EXPORT, export_monitoring)

    except Exception as err:
        # Uma falha de agendamento jamais pode afetar o endpoint.
        logger.warning('Agendamento ignorado por erro: %s', err)
