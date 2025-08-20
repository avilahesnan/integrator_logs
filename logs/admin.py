from django.contrib import admin
from .models import LogSync


@admin.register(LogSync)
class LogSyncAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'city', 'store_id', 'item_quantity', 'sync_date',)
    list_filter = ('city', 'version', 'type_access', 'system',)
    ordering = ('-sync_date', '-item_quantity',)
