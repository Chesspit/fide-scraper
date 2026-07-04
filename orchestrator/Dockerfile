FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached)
COPY orchestrator/requirements.txt ./orchestrator/requirements.txt
RUN pip install --no-cache-dir -r orchestrator/requirements.txt

# Copy project source
COPY orchestrator/ ./orchestrator/
COPY scraper/     ./scraper/
COPY scripts/     ./scripts/
COPY config.yaml  .

# worker_state.json + runtime_settings.json live in /data (mounted volume);
# die Queue selbst liegt seit Review #5 in PostgreSQL (Schema "orchestrator")
ENV ORCHESTRATOR_DATA_DIR=/data

EXPOSE 8050
