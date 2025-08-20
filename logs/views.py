from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from .models import LogSync


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

    if not store_id:
        return Response({'erro': f'Incomplete data: {pharmacy} - {city}'}, status=status.HTTP_400_BAD_REQUEST)

    log, created = LogSync.objects.update_or_create(
        store_id=store_id,
        defaults={
            'pharmacy': pharmacy,
            'city': city,
            'version': version,
            'type_access': type_access,
            'system': system,
            'item_quantity': item_quantity,
            'sync_date': None
        }
    )

    if created:
        msg = 'Log created successfully'
    else:
        msg = 'Log updated successfully'

    return Response({'message': msg}, status=status.HTTP_200_OK)



@api_view(['GET'])
def views_logs(request):
    logs = LogSync.objects.all().order_by('-sync_date')
    data = [
        {
            'store_id': log.store_id,
            'pharmacy': log.pharmacy,
            'city': log.city,
            'version': log.version,
            'type_access': log.type_access,
            'system': log.system,
            'item_quantity': log.item_quantity,
            'sync_date': log.sync_date
        }
        for log in logs
    ]

    return Response(data)
