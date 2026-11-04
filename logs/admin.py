from django.contrib import admin
from django.db.models import OuterRef, Subquery

from .models import LogSync, Pharmacy, SyncState


@admin.register(LogSync)
class LogSyncAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'city', 'store_id', 'item_quantity', 'status', 'sync_date',)
    list_filter = ('city', 'version', 'type_access', 'system', 'status',)
    search_fields = ('pharmacy', 'store_id', 'city')
    ordering = ('-sync_date', '-item_quantity',)
    list_display_links = ('pharmacy',)
    date_hierarchy = 'sync_date'
    list_per_page = 10
    readonly_fields = [f.name for f in LogSync._meta.fields]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        latest = LogSync.objects.filter(
            store_id=OuterRef('store_id')
        ).order_by('-sync_date')

        return qs.filter(id__in=Subquery(latest.values('id')[:1]))


@admin.register(Pharmacy)
class PharmacyAdmin(admin.ModelAdmin):
    list_display = (
        'store_id', 'pharmacy', 'city', 'system', 'type_access',
        'version', 'sheets_synced', 'updated_at',
    )
    list_filter = ('city', 'system', 'type_access', 'version', 'sheets_synced',)
    search_fields = ('store_id', 'pharmacy', 'city')
    ordering = ('pharmacy', 'store_id',)
    list_display_links = ('store_id', 'pharmacy',)
    list_per_page = 25
    readonly_fields = ('created_at', 'updated_at', 'sheets_synced_at', 'sheets_error',)


@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = ('task', 'last_run_date', 'last_run_at', 'last_status',)
    readonly_fields = [f.name for f in SyncState._meta.fields]
