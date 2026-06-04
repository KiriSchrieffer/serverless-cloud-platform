FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY backend ./backend
COPY worker ./worker
COPY runtime ./runtime

RUN pip install --no-cache-dir -e ".[worker]"

CMD ["python", "-m", "worker.app.main"]
