import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aistars.c_settings.prod')

app = Celery('aistars')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
