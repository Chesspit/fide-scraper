FROM python:3.12-slim

# Nur die Abhängigkeiten liegen im Image. Code, Parquet-Caches (frontend/*.parquet,
# gitignored, einzige Kopie für /c und /dist) und data/ kommen per Bind-Mount aus
# dem Checkout /opt/fide-scraper — Deploy = git pull + Neustart, Caches überleben.
WORKDIR /app/frontend

COPY frontend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Die Seiten importieren ihre Datenmodule relativ (import data_qc …), deshalb
# läuft gunicorn im Ordner frontend/ und lädt app:server.
EXPOSE 8055
CMD ["gunicorn", "app:server", "--bind", "0.0.0.0:8055", "--workers", "1", "--threads", "4", "--timeout", "180"]
