PYTHON ?= python3

.PHONY: api worker frontend test benchmark runtime-image compose-up compose-down

api:
	uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PYTHON) -m worker.app.main

frontend:
	npm --prefix frontend run dev

test:
	pytest

benchmark:
	$(PYTHON) benchmarks/run_benchmark.py

runtime-image:
	docker build -t serverless-python311-runtime:latest -f runtime/python311/Dockerfile .

compose-up: runtime-image
	docker compose up --build

compose-down:
	docker compose down
