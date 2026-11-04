"""URL configuration for core project."""
from django.contrib import admin
from django.urls import path, re_path

from logs.views import (
    index,
    last_logs,
    pharmacies_list,
    pharmacy_create,
    pharmacy_detail,
    pharmacy_logs,
    pharmacy_update,
    receive_log,
    sheets_export,
    sheets_import,
    sheets_retry,
    sheets_retry_pending,
    sheets_status,
)


urlpatterns = [
    path('', index, name='index'),
    path('admin-management/', admin.site.urls),

    # Endpoints existentes — inalterados.
    path('api/log-sync/', receive_log, name='receive_log'),
    path('api/last-logs/', last_logs, name='last_logs'),
    path('api/logs/<str:store_id>/', pharmacy_logs, name='pharmacy_logs'),

    # Gerenciamento de farmácias.
    path('api/pharmacies/', pharmacies_list, name='pharmacies_list'),
    path('api/pharmacies/create/', pharmacy_create, name='pharmacy_create'),
    path('api/pharmacies/<str:store_id>/', pharmacy_detail, name='pharmacy_detail'),
    path('api/pharmacies/<str:store_id>/update/', pharmacy_update, name='pharmacy_update'),
    path('api/pharmacies/<str:store_id>/retry-sheets/', sheets_retry, name='sheets_retry'),

    # Integração com o Google Sheets.
    path('api/sheets/status/', sheets_status, name='sheets_status'),
    path('api/sheets/import/', sheets_import, name='sheets_import'),
    path('api/sheets/export/', sheets_export, name='sheets_export'),
    path('api/sheets/retry-pending/', sheets_retry_pending, name='sheets_retry_pending'),

    # Catch-all: precisa continuar sendo a última rota.
    re_path(r'^.*$', index),
]
