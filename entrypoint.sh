#!/bin/sh
set -e

# --clear empties STATIC_ROOT first. It is a host mount, so without this a
# renamed or deleted asset stays there and keeps being served.
python manage.py collectstatic --noinput --clear
python manage.py migrate

exec uvicorn aistars.asgi:application --host 0.0.0.0 --port 8000
