from django.db import models


class LogSync(models.Model):
    store_id = models.CharField(max_length=15, unique=True)
    pharmacy = models.CharField(max_length=255)
    city = models.CharField(max_length=255)
    version = models.CharField(max_length=15)
    type_access = models.CharField(max_length=255)
    system = models.CharField(max_length=255)
    item_quantity = models.IntegerField()
    sync_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.pharmacy} - {self.store_id} - {self.sync_date}'
