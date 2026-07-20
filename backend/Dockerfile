FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY requirements/constraints.txt requirements/constraints.txt
COPY alembic.ini ./
COPY backend ./backend

RUN pip install --no-cache-dir --constraint requirements/constraints.txt -e .

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
