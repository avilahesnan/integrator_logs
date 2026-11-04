"""Verificações do painel administrativo.

Cobrem três coisas: que o endpoint que as farmácias usam continua funcionando
exatamente como antes, que o cadastro e a edição funcionam, e que uma falha no
Google Sheets nunca derruba nem desfaz o cadastro local.
"""

import json
import os
import tempfile

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import LogSync, Pharmacy, SyncState

TOKEN = 'token-de-teste'

PAYLOAD_ORIGINAL = {
    'store_id': '7868',
    'pharmacy': 'Mais Saúde',
    'city': 'Tobias Barreto',
    'version': 'v4',
    'type_access': 'API',
    'system': 'Trier SGF',
    'item_quantity': 743,
}

CADASTRO = {
    'store_id': '9999',
    'pharmacy': 'Farmácia Teste',
    'city': 'Aracaju',
    'system': 'Linx',
    'type_access': 'DB',
    'version': 'v4',
}


def _set_sync_date(log, moment):
    """``sync_date`` é auto_now, então só dá para ajustar por UPDATE direto."""
    LogSync.objects.filter(pk=log.pk).update(sync_date=moment)


@mock.patch.dict('os.environ', {'SHEETS_SYNC_ENABLED': 'False'})
class EndpointExistenteTests(TestCase):
    """O endpoint usado pelas farmácias não pode ter mudado de comportamento."""

    def test_payload_original_continua_aceito(self):
        response = self.client.post(
            '/api/log-sync/', PAYLOAD_ORIGINAL, content_type='application/json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {'message': 'Log created successfully'})

        log = LogSync.objects.get()
        self.assertEqual(log.store_id, '7868')
        self.assertEqual(log.item_quantity, 743)
        # Quem não envia 'status' é tratado como sucesso.
        self.assertEqual(log.status, LogSync.STATUS_SUCCESS)

    def test_sem_store_id_continua_400(self):
        response = self.client.post(
            '/api/log-sync/', {'pharmacy': 'X'}, content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Incomplete data'})
        self.assertEqual(LogSync.objects.count(), 0)

    def test_status_opcional_e_aceito(self):
        payload = dict(PAYLOAD_ORIGINAL, status='error')
        response = self.client.post(
            '/api/log-sync/', payload, content_type='application/json'
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(LogSync.objects.get().status, LogSync.STATUS_ERROR)

    def test_status_invalido_cai_no_padrao(self):
        payload = dict(PAYLOAD_ORIGINAL, status='qualquer-coisa')
        self.client.post('/api/log-sync/', payload, content_type='application/json')

        self.assertEqual(LogSync.objects.get().status, LogSync.STATUS_SUCCESS)

    def test_last_logs_traz_uma_linha_por_loja(self):
        for quantity in (10, 20, 30):
            self.client.post(
                '/api/log-sync/',
                dict(PAYLOAD_ORIGINAL, item_quantity=quantity),
                content_type='application/json',
            )

        self.client.post(
            '/api/log-sync/',
            dict(PAYLOAD_ORIGINAL, store_id='1234', item_quantity=5),
            content_type='application/json',
        )

        data = self.client.get('/api/last-logs/').json()

        self.assertEqual(len(data), 2)
        por_loja = {item['store_id']: item for item in data}
        self.assertEqual(por_loja['7868']['item_quantity'], 30)
        self.assertEqual(por_loja['1234']['item_quantity'], 5)
        # Os campos originais seguem todos presentes.
        for campo in PAYLOAD_ORIGINAL:
            self.assertIn(campo, por_loja['7868'])

    def test_historico_da_farmacia(self):
        for _ in range(3):
            self.client.post(
                '/api/log-sync/', PAYLOAD_ORIGINAL, content_type='application/json'
            )

        data = self.client.get('/api/logs/7868/').json()

        self.assertEqual(len(data), 3)

    def test_requisicoes_continuam_sendo_gravadas_com_sheets_quebrado(self):
        """Uma falha na integração não pode impedir o registro da requisição."""
        with mock.patch(
            'logs.scheduler.ensure_daily_tasks', side_effect=RuntimeError('boom')
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    '/api/log-sync/', PAYLOAD_ORIGINAL, content_type='application/json'
                )

        # Mesmo com o agendador explodindo, o log já tinha sido gravado antes.
        self.assertEqual(LogSync.objects.count(), 1)


@mock.patch.dict('os.environ', {'ADMIN_API_TOKEN': TOKEN, 'SHEETS_SYNC_ENABLED': 'False'})
class CadastroTests(TestCase):
    """Cadastro e edição de farmácias."""

    def _post(self, payload, token=TOKEN):
        headers = {'HTTP_X_ADMIN_TOKEN': token} if token else {}

        return self.client.post(
            '/api/pharmacies/create/',
            payload,
            content_type='application/json',
            **headers,
        )

    def test_cadastro_exige_token(self):
        response = self.client.post(
            '/api/pharmacies/create/', CADASTRO, content_type='application/json'
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Pharmacy.objects.count(), 0)

    def test_token_errado_e_rejeitado(self):
        self.assertEqual(self._post(CADASTRO, token='errado').status_code, 401)
        self.assertEqual(Pharmacy.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_sem_token_configurado_no_servidor_responde_503(self):
        with mock.patch.dict('os.environ', {'ADMIN_API_TOKEN': ''}):
            response = self._post(CADASTRO)

        self.assertEqual(response.status_code, 503)

    def test_cadastro_com_token(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            response = self._post(CADASTRO)

        self.assertEqual(response.status_code, 201)

        pharmacy = Pharmacy.objects.get()
        self.assertEqual(pharmacy.store_id, '9999')
        self.assertEqual(pharmacy.pharmacy, 'Farmácia Teste')
        self.assertTrue(pharmacy.sheets_synced)
        self.assertEqual(pharmacy.sheets_error, '')
        self.assertEqual(response.json()['sheets'], {'synced': True, 'error': None})

    def test_store_id_duplicado_e_recusado(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            self.assertEqual(self._post(CADASTRO).status_code, 201)

            duplicado = dict(CADASTRO, pharmacy='Outro Nome')
            response = self._post(duplicado)

        self.assertEqual(response.status_code, 400)
        self.assertIn('store_id', response.json())
        self.assertEqual(Pharmacy.objects.count(), 1)
        self.assertEqual(Pharmacy.objects.get().pharmacy, 'Farmácia Teste')

    def test_acesso_invalido_e_recusado(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            response = self._post(dict(CADASTRO, type_access='FTP'))

        self.assertEqual(response.status_code, 400)
        self.assertIn('type_access', response.json())

    def test_campos_obrigatorios(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            response = self._post({'store_id': '  '})

        self.assertEqual(response.status_code, 400)

    def test_edicao_atualiza_local_e_sheets(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            self._post(CADASTRO)

            response = self.client.put(
                '/api/pharmacies/9999/update/',
                dict(CADASTRO, city='Itabaiana', version='v5'),
                content_type='application/json',
                HTTP_X_ADMIN_TOKEN=TOKEN,
            )

        self.assertEqual(response.status_code, 200)

        pharmacy = Pharmacy.objects.get()
        self.assertEqual(pharmacy.city, 'Itabaiana')
        self.assertEqual(pharmacy.version, 'v5')
        self.assertTrue(pharmacy.sheets_synced)
        # Continua sendo o mesmo registro: a edição não duplica.
        self.assertEqual(Pharmacy.objects.count(), 1)
        self.assertEqual(fake.push_pharmacies.call_count, 2)

    def test_edicao_de_farmacia_inexistente(self):
        response = self.client.put(
            '/api/pharmacies/0000/update/',
            CADASTRO,
            content_type='application/json',
            HTTP_X_ADMIN_TOKEN=TOKEN,
        )

        self.assertEqual(response.status_code, 404)


@mock.patch.dict('os.environ', {'ADMIN_API_TOKEN': TOKEN, 'SHEETS_SYNC_ENABLED': 'False'})
class FalhaNoSheetsTests(TestCase):
    """Uma falha no Sheets não pode custar o cadastro local."""

    def test_cadastro_sobrevive_a_falha_no_sheets(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            fake.push_pharmacies.side_effect = RuntimeError('API fora do ar')

            response = self.client.post(
                '/api/pharmacies/create/',
                CADASTRO,
                content_type='application/json',
                HTTP_X_ADMIN_TOKEN=TOKEN,
            )

        # O cadastro foi criado mesmo assim.
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Pharmacy.objects.count(), 1)

        pharmacy = Pharmacy.objects.get()
        self.assertFalse(pharmacy.sheets_synced)
        self.assertIn('API fora do ar', pharmacy.sheets_error)
        self.assertFalse(response.json()['sheets']['synced'])

    def test_nova_tentativa_depois_da_falha(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            fake.push_pharmacies.side_effect = RuntimeError('API fora do ar')
            self.client.post(
                '/api/pharmacies/create/',
                CADASTRO,
                content_type='application/json',
                HTTP_X_ADMIN_TOKEN=TOKEN,
            )

        self.assertFalse(Pharmacy.objects.get().sheets_synced)

        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            response = self.client.post(
                '/api/pharmacies/9999/retry-sheets/',
                content_type='application/json',
                HTTP_X_ADMIN_TOKEN=TOKEN,
            )

        self.assertEqual(response.status_code, 200)

        pharmacy = Pharmacy.objects.get()
        self.assertTrue(pharmacy.sheets_synced)
        self.assertEqual(pharmacy.sheets_error, '')

    def test_reenvio_em_lote_dos_pendentes(self):
        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            fake.push_pharmacies.side_effect = RuntimeError('fora do ar')

            for store_id in ('1111', '2222'):
                self.client.post(
                    '/api/pharmacies/create/',
                    dict(CADASTRO, store_id=store_id),
                    content_type='application/json',
                    HTTP_X_ADMIN_TOKEN=TOKEN,
                )

        self.assertEqual(Pharmacy.objects.filter(sheets_synced=False).count(), 2)

        with mock.patch('logs.views.sheets') as fake:
            fake.is_enabled.return_value = True
            response = self.client.post(
                '/api/sheets/retry-pending/',
                content_type='application/json',
                HTTP_X_ADMIN_TOKEN=TOKEN,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['retried'], 2)
        self.assertEqual(Pharmacy.objects.filter(sheets_synced=False).count(), 0)


@mock.patch.dict('os.environ', {'SHEETS_SYNC_ENABLED': 'False'})
class MonitoramentoTests(TestCase):
    """O painel distingue cadastro de comunicação real."""

    def test_cadastrada_sem_requisicao_nao_conta_como_ativa(self):
        Pharmacy.objects.create(**CADASTRO)

        data = self.client.get('/api/pharmacies/').json()

        self.assertEqual(len(data), 1)
        linha = data[0]
        self.assertTrue(linha['registered'])
        self.assertIsNone(linha['last_request'])
        self.assertIsNone(linha['last_sync'])
        self.assertEqual(linha['requests_today'], 0)
        self.assertEqual(linha['activity_status'], 'never')

    def test_loja_sem_cadastro_continua_aparecendo(self):
        """Nada que já era exibido pode sumir do painel."""
        self.client.post(
            '/api/log-sync/', PAYLOAD_ORIGINAL, content_type='application/json'
        )

        data = self.client.get('/api/pharmacies/').json()

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['store_id'], '7868')
        self.assertFalse(data[0]['registered'])
        self.assertEqual(data[0]['pharmacy'], 'Mais Saúde')
        self.assertEqual(data[0]['activity_status'], 'active')

    def test_cadastro_tem_prioridade_sobre_o_log(self):
        self.client.post(
            '/api/log-sync/', PAYLOAD_ORIGINAL, content_type='application/json'
        )
        Pharmacy.objects.create(
            store_id='7868',
            pharmacy='Mais Saúde Centro',
            city='Tobias Barreto',
            system='Trier SGF',
            type_access='API',
            version='v3',
        )

        linha = self.client.get('/api/pharmacies/').json()[0]

        # Nome vem do cadastro, que é o que você mantém.
        self.assertEqual(linha['pharmacy'], 'Mais Saúde Centro')
        # Versão vem do log, que é a que está de fato rodando.
        self.assertEqual(linha['version'], 'v4')
        self.assertEqual(linha['registered_version'], 'v3')
        self.assertEqual(linha['item_quantity'], 743)

    def test_ultima_requisicao_difere_da_ultima_sync(self):
        """Com um erro mais recente, as duas colunas divergem de verdade."""
        sucesso = LogSync.objects.create(status=LogSync.STATUS_SUCCESS, **PAYLOAD_ORIGINAL)
        _set_sync_date(sucesso, timezone.now() - timedelta(minutes=30))

        erro = LogSync.objects.create(status=LogSync.STATUS_ERROR, **PAYLOAD_ORIGINAL)
        _set_sync_date(erro, timezone.now() - timedelta(minutes=5))

        linha = self.client.get('/api/pharmacies/').json()[0]

        self.assertNotEqual(linha['last_request'], linha['last_sync'])
        self.assertGreater(linha['last_request'], linha['last_sync'])
        self.assertEqual(linha['requests_today'], 2)

    def test_status_de_atraso_e_de_sem_comunicacao(self):
        atrasada = LogSync.objects.create(**dict(PAYLOAD_ORIGINAL, store_id='111'))
        _set_sync_date(atrasada, timezone.now() - timedelta(hours=2))

        sumida = LogSync.objects.create(**dict(PAYLOAD_ORIGINAL, store_id='222'))
        _set_sync_date(sumida, timezone.now() - timedelta(days=3))

        por_loja = {
            item['store_id']: item for item in self.client.get('/api/pharmacies/').json()
        }

        self.assertEqual(por_loja['111']['activity_status'], 'late')
        self.assertEqual(por_loja['222']['activity_status'], 'offline')
        self.assertEqual(por_loja['222']['requests_today'], 0)


class ImportacaoDoSheetsTests(TestCase):
    """Repovoamento do SQLite a partir da planilha."""

    LINHAS = [
        {
            'store_id': '9999',
            'pharmacy': 'Farmácia Teste',
            'city': 'Aracaju',
            'system': 'Linx',
            'type_access': 'DB',
            'version': 'v4',
        },
        {
            'store_id': '8888',
            'pharmacy': 'Outra Farmácia',
            'city': 'Estância',
            'system': 'Alpha7',
            'type_access': 'API',
            'version': 'v4',
        },
    ]

    def test_importacao_cria_e_nao_duplica(self):
        from . import scheduler

        with mock.patch('logs.sheets.fetch_pharmacies', return_value=self.LINHAS):
            scheduler.import_pharmacies()
            # Rodar duas vezes não pode duplicar nada.
            scheduler.import_pharmacies()

        self.assertEqual(Pharmacy.objects.count(), 2)
        self.assertEqual(Pharmacy.objects.get(store_id='9999').city, 'Aracaju')
        self.assertTrue(Pharmacy.objects.get(store_id='9999').sheets_synced)

    def test_importacao_atualiza_registro_existente(self):
        from . import scheduler

        Pharmacy.objects.create(**dict(CADASTRO, city='Cidade Velha', sheets_synced=True))

        with mock.patch('logs.sheets.fetch_pharmacies', return_value=self.LINHAS):
            scheduler.import_pharmacies()

        self.assertEqual(Pharmacy.objects.count(), 2)
        self.assertEqual(Pharmacy.objects.get(store_id='9999').city, 'Aracaju')

    def test_importacao_preserva_alteracao_local_pendente(self):
        """Um cadastro que ainda não subiu para a planilha não é sobrescrito."""
        from . import scheduler

        Pharmacy.objects.create(
            **dict(CADASTRO, city='Edição Local', sheets_synced=False)
        )

        with mock.patch('logs.sheets.fetch_pharmacies', return_value=self.LINHAS):
            scheduler.import_pharmacies()

        self.assertEqual(Pharmacy.objects.get(store_id='9999').city, 'Edição Local')
        self.assertEqual(Pharmacy.objects.count(), 2)

    def test_linha_sem_store_id_e_ignorada(self):
        from . import sheets as sheets_module

        worksheet = mock.Mock()
        worksheet.get_all_records.return_value = [
            {'store_id': '', 'pharmacy': 'Sem id'},
            {'store_id': '7777', 'pharmacy': 'Com id', 'city': 'X',
             'system': 'Y', 'type_access': 'db', 'version': 'v4'},
        ]

        with mock.patch.object(sheets_module, '_get_worksheet', return_value=worksheet):
            registros = sheets_module.fetch_pharmacies()

        self.assertEqual(len(registros), 1)
        self.assertEqual(registros[0]['store_id'], '7777')
        # O acesso é normalizado para maiúsculas.
        self.assertEqual(registros[0]['type_access'], 'DB')


class AgendamentoTests(TestCase):
    """A trava diária garante uma execução por dia, mesmo com vários workers."""

    def test_tarefa_do_dia_so_e_reivindicada_uma_vez(self):
        from . import scheduler

        hoje = timezone.localdate()

        self.assertTrue(scheduler._claim(SyncState.TASK_EXPORT, hoje))
        self.assertFalse(scheduler._claim(SyncState.TASK_EXPORT, hoje))
        self.assertFalse(scheduler._claim(SyncState.TASK_EXPORT, hoje))

        # No dia seguinte roda de novo.
        self.assertTrue(scheduler._claim(SyncState.TASK_EXPORT, hoje + timedelta(days=1)))

    def test_falha_libera_a_trava_para_nova_tentativa(self):
        from . import scheduler

        def explode():
            raise RuntimeError('sem rede')

        scheduler._claim(SyncState.TASK_EXPORT, timezone.localdate())
        scheduler._run(SyncState.TASK_EXPORT, explode)

        state = SyncState.objects.get(task=SyncState.TASK_EXPORT)
        self.assertIsNone(state.last_run_date)
        self.assertEqual(state.last_status, SyncState.STATUS_ERROR)
        self.assertIn('sem rede', state.last_message)

    @mock.patch.dict('os.environ', {'SHEETS_SYNC_ENABLED': 'False'})
    def test_agendamento_desligado_nao_faz_nada(self):
        from . import scheduler

        scheduler.ensure_daily_tasks()

        self.assertEqual(SyncState.objects.count(), 0)

    def test_exportacao_consolida_uma_linha_por_farmacia(self):
        from . import scheduler

        for _ in range(3):
            LogSync.objects.create(**PAYLOAD_ORIGINAL)

        with mock.patch('logs.sheets.push_monitoring', return_value=(1, 0)) as push:
            scheduler.export_monitoring()

        linhas = push.call_args[0][0]
        # Três requisições, mas uma única linha no consolidado.
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]['store_id'], '7868')
        self.assertEqual(linhas[0]['requests_today'], 3)


class SheetsUpsertTests(TestCase):
    """O upsert casa pelo store_id e nunca cria uma linha por requisição."""

    def test_upsert_atualiza_existente_e_acrescenta_nova(self):
        from . import sheets as sheets_module

        worksheet = mock.Mock()
        worksheet.title = 'Cadastros'
        worksheet.get_all_values.return_value = [
            sheets_module.CADASTROS_HEADER,
            ['9999', 'Nome Antigo', 'Aracaju', 'Linx', 'DB', 'v3', ''],
        ]

        linhas = [
            {'store_id': '9999', 'pharmacy': 'Nome Novo', 'city': 'Aracaju',
             'system': 'Linx', 'type_access': 'DB', 'version': 'v4', 'updated_at': ''},
            {'store_id': '8888', 'pharmacy': 'Nova', 'city': 'Estância',
             'system': 'Alpha7', 'type_access': 'API', 'version': 'v4', 'updated_at': ''},
        ]

        atualizadas, inseridas = sheets_module._upsert_rows(
            worksheet, sheets_module.CADASTROS_HEADER, linhas
        )

        self.assertEqual((atualizadas, inseridas), (1, 1))

        # A existente foi atualizada na própria linha 2, não acrescentada.
        update = worksheet.batch_update.call_args[0][0][0]
        self.assertEqual(update['range'], 'A2:G2')
        self.assertEqual(update['values'][0][1], 'Nome Novo')

        acrescentadas = worksheet.append_rows.call_args[0][0]
        self.assertEqual(len(acrescentadas), 1)
        self.assertEqual(acrescentadas[0][0], '8888')

    def test_colunas_extras_da_planilha_sao_preservadas(self):
        """Colunas acrescentadas à mão não podem ser apagadas pelo upsert."""
        from . import sheets as sheets_module

        header = sheets_module.CADASTROS_HEADER
        worksheet = mock.Mock()
        worksheet.title = 'Cadastros'
        worksheet.get_all_values.return_value = [
            header + ['observacao'],
            ['9999', 'Nome Antigo', 'Aracaju', 'Linx', 'DB', 'v3', '', 'anotação minha'],
        ]

        sheets_module._upsert_rows(worksheet, header, [{
            'store_id': '9999', 'pharmacy': 'Nome Novo', 'city': 'Aracaju',
            'system': 'Linx', 'type_access': 'DB', 'version': 'v4', 'updated_at': '',
        }])

        gravado = worksheet.batch_update.call_args[0][0][0]['values'][0]

        self.assertEqual(gravado[1], 'Nome Novo')
        self.assertEqual(gravado[-1], 'anotação minha')

    def test_conversao_de_indice_para_letra_de_coluna(self):
        from . import sheets as sheets_module

        self.assertEqual(sheets_module._column_letter(1), 'A')
        self.assertEqual(sheets_module._column_letter(7), 'G')
        self.assertEqual(sheets_module._column_letter(11), 'K')
        self.assertEqual(sheets_module._column_letter(26), 'Z')
        self.assertEqual(sheets_module._column_letter(27), 'AA')


class ConfiguracaoTests(TestCase):
    """Sem configuração, a integração degrada com mensagem clara."""

    @mock.patch.dict('os.environ', {
        'GOOGLE_SHEETS_ID': '',
        'GOOGLE_SERVICE_ACCOUNT_JSON': '',
        'GOOGLE_SERVICE_ACCOUNT_FILE': '/caminho/que/nao/existe.json',
    })
    def test_status_informa_o_que_falta(self):
        data = self.client.get('/api/sheets/status/').json()

        self.assertFalse(data['enabled'])
        self.assertIn('GOOGLE_SHEETS_ID', data['configuration_error'])
        self.assertIn('GOOGLE_SERVICE_ACCOUNT_JSON', data['configuration_error'])

    @mock.patch.dict('os.environ', {
        'ADMIN_API_TOKEN': TOKEN,
        'GOOGLE_SHEETS_ID': '',
        'GOOGLE_SERVICE_ACCOUNT_JSON': '',
        'GOOGLE_SERVICE_ACCOUNT_FILE': '/caminho/que/nao/existe.json',
    })
    def test_importacao_manual_sem_configuracao_responde_503(self):
        response = self.client.post(
            '/api/sheets/import/',
            content_type='application/json',
            HTTP_X_ADMIN_TOKEN=TOKEN,
        )

        self.assertEqual(response.status_code, 503)


class CredenciaisTests(TestCase):
    """De onde as credenciais da conta de serviço são lidas.

    Duas origens: a variável com o JSON inline e um arquivo no disco, que é
    como o Render entrega um Secret File (/etc/secrets/<nome>).
    """

    def setUp(self):
        self.diretorio = tempfile.TemporaryDirectory()
        self.addCleanup(self.diretorio.cleanup)

        self.arquivo = os.path.join(self.diretorio.name, 'google-credentials.json')
        self.conteudo = json.dumps({
            'type': 'service_account',
            'project_id': 'projeto-teste',
            'private_key': '-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n',
            'client_email': 'painel@projeto-teste.iam.gserviceaccount.com',
            'token_uri': 'https://oauth2.googleapis.com/token',
        })

        with open(self.arquivo, 'w', encoding='utf-8') as handle:
            handle.write(self.conteudo)

    def test_le_as_credenciais_do_arquivo(self):
        from . import sheets as sheets_module

        with mock.patch.dict('os.environ', {
            'GOOGLE_SERVICE_ACCOUNT_JSON': '',
            'GOOGLE_SERVICE_ACCOUNT_FILE': self.arquivo,
        }):
            bruto, origem = sheets_module._read_service_account()

        self.assertEqual(json.loads(bruto)['project_id'], 'projeto-teste')
        self.assertIn(self.arquivo, origem)

    def test_arquivo_habilita_a_integracao_sem_a_variavel_inline(self):
        from . import sheets as sheets_module

        with mock.patch.dict('os.environ', {
            'GOOGLE_SHEETS_ID': 'planilha-123',
            'GOOGLE_SERVICE_ACCOUNT_JSON': '',
            'GOOGLE_SERVICE_ACCOUNT_FILE': self.arquivo,
            'SHEETS_SYNC_ENABLED': 'True',
        }):
            self.assertTrue(sheets_module.is_enabled())
            self.assertIsNone(sheets_module.configuration_error())
            self.assertEqual(sheets_module.credential_source(), self.arquivo)

    def test_variavel_inline_vence_sobre_o_arquivo(self):
        from . import sheets as sheets_module

        with mock.patch.dict('os.environ', {
            'GOOGLE_SERVICE_ACCOUNT_JSON': '{"project_id": "veio-da-variavel"}',
            'GOOGLE_SERVICE_ACCOUNT_FILE': self.arquivo,
        }):
            bruto, origem = sheets_module._read_service_account()

        self.assertEqual(json.loads(bruto)['project_id'], 'veio-da-variavel')
        self.assertIn('GOOGLE_SERVICE_ACCOUNT_JSON', origem)

    def test_caminho_padrao_e_o_secret_file_do_render(self):
        from . import sheets as sheets_module

        with mock.patch.dict('os.environ', {'GOOGLE_SERVICE_ACCOUNT_FILE': ''}):
            self.assertEqual(
                sheets_module.service_account_file(),
                '/etc/secrets/google-credentials.json',
            )

    def test_arquivo_ausente_nao_habilita_e_explica_o_que_falta(self):
        from . import sheets as sheets_module

        with mock.patch.dict('os.environ', {
            'GOOGLE_SHEETS_ID': 'planilha-123',
            'GOOGLE_SERVICE_ACCOUNT_JSON': '',
            'GOOGLE_SERVICE_ACCOUNT_FILE': '/caminho/que/nao/existe.json',
            'SHEETS_SYNC_ENABLED': 'True',
        }):
            self.assertFalse(sheets_module.is_enabled())
            self.assertIsNone(sheets_module.credential_source())

            erro = sheets_module.configuration_error()

        self.assertIn('GOOGLE_SERVICE_ACCOUNT_JSON', erro)
        self.assertIn('/caminho/que/nao/existe.json', erro)

    def test_arquivo_ilegivel_da_mensagem_apontando_o_caminho(self):
        from . import sheets as sheets_module

        with mock.patch.dict('os.environ', {
            'GOOGLE_SERVICE_ACCOUNT_JSON': '',
            'GOOGLE_SERVICE_ACCOUNT_FILE': '/caminho/que/nao/existe.json',
        }):
            with self.assertRaises(sheets_module.SheetsError) as contexto:
                sheets_module._read_service_account()

        self.assertIn('/caminho/que/nao/existe.json', str(contexto.exception))
        self.assertIn('/etc/secrets/', str(contexto.exception))

    def test_arquivo_vazio_e_tratado_como_erro_claro(self):
        from . import sheets as sheets_module

        vazio = os.path.join(self.diretorio.name, 'vazio.json')

        with open(vazio, 'w', encoding='utf-8') as handle:
            handle.write('   \n')

        with mock.patch.dict('os.environ', {
            'GOOGLE_SERVICE_ACCOUNT_JSON': '',
            'GOOGLE_SERVICE_ACCOUNT_FILE': vazio,
        }):
            with self.assertRaises(sheets_module.SheetsError) as contexto:
                sheets_module._read_service_account()

        self.assertIn('está vazio', str(contexto.exception))

    def test_status_informa_a_origem_das_credenciais(self):
        with mock.patch.dict('os.environ', {
            'GOOGLE_SHEETS_ID': 'planilha-123',
            'GOOGLE_SERVICE_ACCOUNT_JSON': '',
            'GOOGLE_SERVICE_ACCOUNT_FILE': self.arquivo,
            'SHEETS_SYNC_ENABLED': 'True',
        }):
            data = self.client.get('/api/sheets/status/').json()

        self.assertTrue(data['enabled'])
        self.assertIsNone(data['configuration_error'])
        self.assertEqual(data['credentials_source'], self.arquivo)
