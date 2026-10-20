FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
        libpq-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

RUN groupadd -g 33 www-data || true
RUN useradd -u 1000 -g 33 ubuntu || true

COPY --chown=ubuntu:www-data . /app/

RUN mkdir -p /app/static && chown -R ubuntu:www-data /app/static

USER ubuntu

EXPOSE 8000

CMD ["sh", "/app/entrypoint.sh"]
