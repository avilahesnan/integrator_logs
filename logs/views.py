import hmac
import logging
import os
from functools import wraps

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import scheduler, sheets
from .models import LogSync, Pharmacy, SyncState
from .monitoring import build_overview
from .serializers import PharmacySerializer

logger = logging.getLogger(__name__)


def require_admin_token(view):
    """Protege as rotas de escrita com um token único vindo do ambiente.

    Não é um sistema de usuários: é uma variável de ambiente compartilhada
    entre o painel e a API. As rotas de leitura e o endpoint que as farmácias
    usam seguem abertos como antes.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        expected = (os.getenv('ADMIN_API_TOKEN') or '').strip()

        if not expected:
            if settings.DEBUG:
                return view(request, *args, **kwargs)

            return Response(
                {'error': 'ADMIN_API_TOKEN não configurado no servidor.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        provided = (request.headers.get('X-Admin-Token') or '').strip()

        if not provided or not hmac.compare_digest(provided, expected):
            return Response(
                {'error': 'Token administrativo inválido ou ausente.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return view(request, *args, **kwargs)

    return wrapper


def _log_to_dict(log):
    return {
        'store_id': log.store_id,
        'pharmacy': log.pharmacy,
        'city': log.city,
        'version': log.version,
        'type_access': log.type_access,
        'system': log.system,
        'item_quantity': log.item_quantity,
        'status': log.status,
        'sync_date': log.sync_date,
    }


def _mirror_to_sheets(pharmacy):
    """Espelha um cadastro no Sheets. Nunca levanta exceção.

    O cadastro local já está gravado quando esta função roda: uma falha aqui
    apenas marca o registro como pendente, para nova tentativa depois.
    """
    if not sheets.is_enabled():
        reason = sheets.configuration_error() or 'Integração com o Sheets não configurada.'
        Pharmacy.objects.filter(pk=pharmacy.pk).update(
            sheets_synced=False,
            sheets_error=reason[:2000],
        )

        return False, reason

    try:
        sheets.push_pharmacies([pharmacy])
    except Exception as err:
        logger.warning('Falha ao espelhar %s no Sheets: %s', pharmacy.store_id, err)
        Pharmacy.objects.filter(pk=pharmacy.pk).update(
            sheets_synced=False,
            sheets_error=str(err)[:2000],
        )

        return False, str(err)

    Pharmacy.objects.filter(pk=pharmacy.pk).update(
        sheets_synced=True,
        sheets_synced_at=timezone.now(),
        sheets_error='',
    )

    return True, None


@api_view(['POST'])
@permission_classes([AllowAny])
def receive_log(request):
    store_id = request.data.get('store_id')
    pharmacy = request.data.get('pharmacy')
    city = request.data.get('city')
    version = request.data.get('version')
    type_access = request.data.get('type_access')
    system = request.data.get('system')
    item_quantity = request.data.get('item_quantity')
    # Opcional e retrocompatível: ausente significa sucesso, que é o que o
    # integrador registra hoje.
    log_status = request.data.get('status') or LogSync.STATUS_SUCCESS

    if not store_id:
        return Response({'error': 'Incomplete data'}, status=status.HTTP_400_BAD_REQUEST)

    if log_status not in dict(LogSync.STATUS_CHOICES):
        log_status = LogSync.STATUS_SUCCESS

    LogSync.objects.create(
        store_id=store_id,
        pharmacy=pharmacy,
        city=city,
        version=version,
        type_access=type_access,
        system=system,
        item_quantity=item_quantity,
        status=log_status,
    )

    # Carona do agendamento diário: roda em thread e nunca atrasa ou derruba
    # esta resposta.
    scheduler.ensure_daily_tasks()

    return Response({'message': 'Log created successfully'}, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def last_logs(request):
    latest_logs = LogSync.objects.filter(
        store_id=OuterRef('store_id')
    ).order_by('-sync_date')

    logs = LogSync.objects.filter(
        id=Subquery(latest_logs.values('id')[:1])
    ).order_by('-sync_date')

    return Response([_log_to_dict(log) for log in logs])


@api_view(['GET'])
def pharmacy_logs(request, store_id):
    logs = LogSync.objects.filter(store_id=store_id).order_by('-sync_date')

    return Response([_log_to_dict(log) for log in logs])


@api_view(['GET'])
def pharmacies_list(request):
    """Tela principal: cadastro + atividade real de cada farmácia."""
    scheduler.ensure_daily_tasks()

    return Response(build_overview())


@api_view(['POST'])
@permission_classes([AllowAny])
@require_admin_token
def pharmacy_create(request):
    """Cadastra uma farmácia e espelha imediatamente no Sheets.

    A falha no Sheets não desfaz o cadastro local: ele fica pendente.
    """
    serializer = PharmacySerializer(data=request.data)

    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    pharmacy = serializer.save()
    synced, error = _mirror_to_sheets(pharmacy)
    pharmacy.refresh_from_db()

    return Response(
        {
            'pharmacy': PharmacySerializer(pharmacy).data,
            'sheets': {'synced': synced, 'error': error},
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(['PUT', 'PATCH'])
@permission_classes([AllowAny])
@require_admin_token
def pharmacy_update(request, store_id):
    """Edita uma farmácia e atualiza o Sheets imediatamente."""
    pharmacy = Pharmacy.objects.filter(store_id=store_id).first()

    if pharmacy is None:
        return Response(
            {'error': f'Farmácia {store_id} não encontrada.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = PharmacySerializer(
        pharmacy,
        data=request.data,
        partial=request.method == 'PATCH',
    )

    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    pharmacy = serializer.save()
    synced, error = _mirror_to_sheets(pharmacy)
    pharmacy.refresh_from_db()

    return Response({
        'pharmacy': PharmacySerializer(pharmacy).data,
        'sheets': {'synced': synced, 'error': error},
    })


@api_view(['GET'])
def pharmacy_detail(request, store_id):
    """Cadastro de uma farmácia, para preencher o formulário de edição."""
    pharmacy = Pharmacy.objects.filter(store_id=store_id).first()

    if pharmacy is None:
        return Response(
            {'error': f'Farmácia {store_id} não encontrada.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(PharmacySerializer(pharmacy).data)


@api_view(['POST'])
@permission_classes([AllowAny])
@require_admin_token
def sheets_retry(request, store_id):
    """Nova tentativa de espelhar um cadastro que falhou."""
    pharmacy = Pharmacy.objects.filter(store_id=store_id).first()

    if pharmacy is None:
        return Response(
            {'error': f'Farmácia {store_id} não encontrada.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    synced, error = _mirror_to_sheets(pharmacy)
    pharmacy.refresh_from_db()

    return Response({
        'pharmacy': PharmacySerializer(pharmacy).data,
        'sheets': {'synced': synced, 'error': error},
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@require_admin_token
def sheets_retry_pending(request):
    """Reenvia todos os cadastros que ficaram pendentes."""
    pending = list(Pharmacy.objects.filter(sheets_synced=False))

    if not pending:
        return Response({'message': 'Nenhum cadastro pendente.', 'retried': 0, 'failed': 0})

    failed = []

    for pharmacy in pending:
        synced, error = _mirror_to_sheets(pharmacy)

        if not synced:
            failed.append({'store_id': pharmacy.store_id, 'error': error})

    return Response({
        'message': f'{len(pending) - len(failed)} de {len(pending)} cadastro(s) reenviado(s).',
        'retried': len(pending) - len(failed),
        'failed': len(failed),
        'errors': failed,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@require_admin_token
def sheets_import(request):
    """Importação manual dos cadastros do Sheets."""
    if not sheets.is_enabled():
        return Response(
            {'error': sheets.configuration_error()},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        message = scheduler.run_import_now()
    except Exception as err:
        logger.exception('Importação manual falhou')

        return Response({'error': str(err)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({'message': message})


@api_view(['POST'])
@permission_classes([AllowAny])
@require_admin_token
def sheets_export(request):
    """Exportação manual do consolidado de monitoramento."""
    if not sheets.is_enabled():
        return Response(
            {'error': sheets.configuration_error()},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        message = scheduler.run_export_now()
    except Exception as err:
        logger.exception('Exportação manual falhou')

        return Response({'error': str(err)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({'message': message})


@api_view(['GET'])
def sheets_status(request):
    """Estado da integração: configuração, últimas execuções e pendências."""
    states = {
        state.task: {
            'last_run_date': state.last_run_date,
            'last_run_at': state.last_run_at,
            'last_status': state.last_status,
            'last_message': state.last_message,
        }
        for state in SyncState.objects.all()
    }

    return Response({
        'enabled': sheets.is_enabled(),
        'configuration_error': sheets.configuration_error(),
        'credentials_source': sheets.credential_source(),
        'export_hour': scheduler.export_hour(),
        'spreadsheet_id': sheets.spreadsheet_id(),
        'tabs': {
            'cadastros': sheets.cadastros_tab(),
            'monitoramento': sheets.monitoramento_tab(),
        },
        'pending_count': Pharmacy.objects.filter(sheets_synced=False).count(),
        'tasks': states,
    })


def index(request):
    html = """
    <!DOCTYPE html>
    <html lang="pt-br">
        <head>
            <meta charset="UTF-8" />
            <title>API de Logs</title>
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <style>
                :root{
                    --red:#c8102e;
                    --yellow:#fff200;
                    --ink:#1b1b1b;
                    --muted:#4a4a4a;
                    --radius:14px;
                }

                * {
                    box-sizing:border-box
                }

                html, body {
                    height:100%
                }

                body{
                    margin:0;
                    font-family: ui-sans-serif, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
                    background:
                        radial-gradient(1200px 800px at 20% -10%, rgba(0,0,0,.15), transparent 60%),
                        var(--red);
                    color:var(--ink);
                    display:grid;
                    place-items:center;
                    padding:24px;
                }

                main{
                    width:100%;
                    max-width:560px;
                    background:linear-gradient(180deg, #fff200, #fff200);
                    border-radius:var(--radius);
                    box-shadow:
                        0 10px 25px rgba(0,0,0,.22),
                        0 2px 10px rgba(0,0,0,.08);
                    border:1px solid rgba(0,0,0,.08);
                    overflow:hidden;
                }

                header{
                    display:flex;
                    align-items:center;
                    gap:12px;
                    padding:22px 24px 10px;
                }

                h1{
                    margin:0;
                    font-size:1.25rem;
                    line-height:1.2;
                    color:var(--red);
                }

                .content{
                    padding:8px 24px 22px;
                }
                
                .card{
                    background:white;
                    border:1px solid rgba(0,0,0,.08);
                    border-radius:12px;
                    padding:14px 16px;
                    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
                    font-size:.95rem;
                    color:#111;
                    box-shadow:0 2px 8px rgba(0,0,0,.06);
                }

                footer{
                    padding:14px 24px 20px;
                    display:flex;
                    justify-content:flex-end;
                    font-size:.8rem;
                    color:rgba(0,0,0,.6);
                }

                @media (max-width:420px){
                    header {
                        padding:18px 16px 8px
                    }

                    .content {
                        padding:8px 16px 18px
                    }

                    footer {
                        padding:12px 16px 16px
                    }
                }
            </style>
        </head>
        <body>
            <main role="main" aria-label="API de Registro de Logs">
                <header>
                    <div>
                        <h1>API de Registro de Logs</h1>
                    </div>
                </header>

                <section class="content">
                    <p class="lead">Este serviço está ativo e pronto para receber registros.</p>

                    <div class="card" aria-label="Orientações sobre a API">
                        Consulte a documentação para orientações sobre a integração de logs.
                    </div>
                </section>

                <footer>
                    API para Logs© <span id="y"></span> 
                </footer>
            </main>

            <script>
                document.getElementById('y').textContent = new Date().getFullYear();
            </script>
        </body>
    </html>
    """
    return HttpResponse(html)
