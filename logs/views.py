from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse
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
