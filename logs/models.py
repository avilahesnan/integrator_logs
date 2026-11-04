from django.db import models


class LogSync(models.Model):
    """Registro de requisições recebidas das farmácias.

    Uma linha por requisição recebida no endpoint ``/api/log-sync/``.
    Esta tabela não é o cadastro da farmácia: ela é a prova de que a
    farmácia realmente se comunicou. O cadastro fica em ``Pharmacy``.
    """

    STATUS_SUCCESS = 'success'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_SUCCESS, 'Sucesso'),
        (STATUS_ERROR, 'Erro'),
    ]

    store_id = models.CharField(max_length=15)
    pharmacy = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    version = models.CharField(max_length=15)
    type_access = models.CharField(max_length=255)
    system = models.CharField(max_length=255)
    item_quantity = models.IntegerField()
    # Campo opcional e retrocompatível: quem não envia 'status' cai no default
    # 'success', que é o comportamento atual do integrador (ele só registra log
    # depois de um envio bem-sucedido ao CSF).
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_SUCCESS,
    )
    sync_date = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['store_id', '-sync_date']),
            models.Index(fields=['-sync_date']),
        ]

    def __str__(self):
        return f'{self.store_id} - {self.pharmacy} - {self.city}'


class Pharmacy(models.Model):
    """Cadastro da farmácia, identificada de forma única pelo ``store_id``.

    O cadastro é independente da atividade: existir aqui não significa que a
    farmácia está se comunicando. A comunicação é medida por ``LogSync``.
    """

    ACCESS_API = 'API'
    ACCESS_DB = 'DB'
    ACCESS_CHOICES = [
        (ACCESS_API, 'API'),
        (ACCESS_DB, 'DB'),
    ]

    store_id = models.CharField(max_length=15, unique=True)
    pharmacy = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    system = models.CharField(max_length=255)
    type_access = models.CharField(max_length=255, choices=ACCESS_CHOICES)
    version = models.CharField(max_length=15)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Estado do espelhamento no Google Sheets. Uma falha aqui nunca impede o
    # cadastro local: o registro fica marcado como pendente e pode ser
    # reenviado depois.
    sheets_synced = models.BooleanField(default=False)
    sheets_synced_at = models.DateTimeField(null=True, blank=True)
    sheets_error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['pharmacy', 'store_id']
        verbose_name = 'Farmácia'
        verbose_name_plural = 'Farmácias'

    def __str__(self):
        return f'{self.store_id} - {self.pharmacy}'


class SyncState(models.Model):
    """Controle de execução das tarefas diárias de sincronização.

    Guarda a última data em que cada tarefa rodou, para que o agendamento por
    carona nas requisições execute no máximo uma vez por dia.
    """

    TASK_IMPORT = 'sheets_import'
    TASK_EXPORT = 'sheets_export'
    TASK_CHOICES = [
        (TASK_IMPORT, 'Importação de cadastros do Sheets'),
        (TASK_EXPORT, 'Exportação do consolidado para o Sheets'),
    ]

    STATUS_OK = 'ok'
    STATUS_ERROR = 'error'

    task = models.CharField(max_length=50, unique=True, choices=TASK_CHOICES)
    last_run_date = models.DateField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=20, blank=True, default='')
    last_message = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Estado de sincronização'
        verbose_name_plural = 'Estados de sincronização'

    def __str__(self):
        return f'{self.task} - {self.last_run_date or "nunca"}'
