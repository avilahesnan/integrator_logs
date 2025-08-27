from django.db.models import OuterRef, Subquery
from django.contrib import admin
from .models import LogSync


@admin.register(LogSync)
class LogSyncAdmin(admin.ModelAdmin):
    list_display = ('pharmacy', 'city', 'store_id', 'item_quantity', 'sync_date',)
    list_filter = ('city', 'version', 'type_access', 'system',)
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
