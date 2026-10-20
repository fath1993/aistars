from django.urls import path

from . import views

# Mounted at /bot/, so the webhook is /bot/webhook/ (runtime.WEBHOOK_PATH).
urlpatterns = [
    path("webhook/", views.telegram_webhook, name="telegram_webhook"),
]
