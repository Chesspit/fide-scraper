FROM python:3.12-slim
WORKDIR /app
COPY scraper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scraper/ ./scraper/
COPY scripts/ ./scripts/
COPY config.yaml .
CMD ["python", "-m", "scraper.main", "run", "--latest"]
