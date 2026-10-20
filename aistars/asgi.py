import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aistars.c_settings.prod")

application = get_asgi_application()
