FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    TZ=Europe/Athens

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 1000 --create-home appuser \
 && mkdir -p /data && chown appuser /data

COPY app ./app
USER appuser

EXPOSE 9999
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9999/healthz', timeout=2)"

# Ένας worker: η SQLite θέλει έναν writer. Για 5–50 χρήστες είναι υπεραρκετό.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9999", "--workers", "1", "--proxy-headers"]
