"""Consolidação do monitoramento a partir dos dados que já existem.

Nada aqui é armazenado: tudo é derivado por agregação sobre ``LogSync`` e
combinado com o cadastro em ``Pharmacy``. Uma farmácia cadastrada que nunca
enviou requisição aparece com atividade vazia — estar cadastrada não é
considerado sinal de comunicação.
"""

import os

from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.utils import timezone

from .models import LogSync, Pharmacy

# Uma farmácia envia requisição a cada ~20 minutos. Passado o limite abaixo
# sem nenhuma requisição, ela é tratada como atrasada; passado um dia, como
# sem comunicação.
DEFAULT_LATE_MINUTES = 45
DEFAULT_OFFLINE_MINUTES = 24 * 60

STATUS_NEVER = 'never'
STATUS_ACTIVE = 'active'
STATUS_LATE = 'late'
STATUS_OFFLINE = 'offline'


def _int_env(name, default):
    try:
        return int(os.getenv(name, '') or default)
    except (TypeError, ValueError):
        return default


def late_minutes():
    return _int_env('MONITOR_LATE_MINUTES', DEFAULT_LATE_MINUTES)


def offline_minutes():
    return _int_env('MONITOR_OFFLINE_MINUTES', DEFAULT_OFFLINE_MINUTES)


def _activity_status(last_request, now):
    if last_request is None:
        return STATUS_NEVER

    elapsed = (now - last_request).total_seconds() / 60

    if elapsed <= late_minutes():
        return STATUS_ACTIVE

    if elapsed <= offline_minutes():
        return STATUS_LATE

    return STATUS_OFFLINE


def build_overview():
    """Uma linha por farmácia, unindo cadastro e atividade real.

    Entram no resultado tanto as farmácias cadastradas quanto os ``store_id``
    que aparecem em ``LogSync`` sem cadastro — assim nada que já era exibido
    deixa de ser exibido.
    """
    now = timezone.now()
    today = timezone.localdate()

    # Agregados por loja: última requisição de qualquer tipo, última
    # sincronização bem-sucedida e contagem de requisições de hoje.
    aggregates = {
        row['store_id']: row
        for row in LogSync.objects.values('store_id').annotate(
            last_request=Max('sync_date'),
            last_sync=Max('sync_date', filter=Q(status=LogSync.STATUS_SUCCESS)),
            requests_today=Count('id', filter=Q(sync_date__date=today)),
            requests_total=Count('id'),
        )
    }

    # Última linha de cada loja, para os campos observados (versão em
    # execução e quantidade de itens).
    newest = LogSync.objects.filter(store_id=OuterRef('store_id')).order_by('-sync_date')
    latest_logs = {
        log.store_id: log
        for log in LogSync.objects.filter(id=Subquery(newest.values('id')[:1]))
    }

    registrations = {item.store_id: item for item in Pharmacy.objects.all()}

    overview = []

    for store_id in sorted(set(registrations) | set(aggregates)):
        registration = registrations.get(store_id)
        latest = latest_logs.get(store_id)
        aggregate = aggregates.get(store_id, {})

        if registration is not None:
            # O cadastro é a fonte de verdade dos campos que você mantém.
            name = registration.pharmacy
            city = registration.city
            system = registration.system
            type_access = registration.type_access
            registered_version = registration.version
        else:
            name = latest.pharmacy if latest else ''
            city = latest.city if latest else ''
            system = latest.system if latest else ''
            type_access = latest.type_access if latest else ''
            registered_version = ''

        # A versão exibida é a observada na última requisição, porque é a que
        # está de fato rodando. Sem requisições, cai para a cadastrada.
        version = latest.version if latest else registered_version

        last_request = aggregate.get('last_request')
        last_sync = aggregate.get('last_sync')

        overview.append({
            'store_id': store_id,
            'pharmacy': name,
            'city': city,
            'system': system,
            'type_access': type_access,
            'version': version,
            'registered_version': registered_version,
            'item_quantity': latest.item_quantity if latest else None,
            'last_sync': last_sync,
            'last_request': last_request,
            'requests_today': aggregate.get('requests_today', 0),
            'requests_total': aggregate.get('requests_total', 0),
            'activity_status': _activity_status(last_request, now),
            'registered': registration is not None,
            'sheets_synced': registration.sheets_synced if registration else False,
            'sheets_error': registration.sheets_error if registration else '',
        })

    return overview
