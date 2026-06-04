PYTHON ?= python3

.PHONY: api worker frontend test compose-up compose-down

api:
	uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PYTHON) -m worker.app.main

frontend:
	npm --prefix frontend run dev

test:
	pytest

compose-up:
	docker compose up --build

compose-down:
	docker compose down
