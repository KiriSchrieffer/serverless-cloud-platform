FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY requirements/constraints.txt requirements/constraints.txt
COPY backend ./backend
COPY worker ./worker
COPY runtime ./runtime

RUN pip install --no-cache-dir --constraint requirements/constraints.txt -e ".[worker]"

CMD ["python", "-m", "worker.app.main"]
