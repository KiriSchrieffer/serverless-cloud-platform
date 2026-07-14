PYTHON ?= python3

.PHONY: api worker frontend test quality integration docker-smoke benchmark runtime-image compose-up compose-down

api:
	uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PYTHON) -m worker.app.main

frontend:
	npm --prefix frontend run dev

test:
	$(PYTHON) -m pytest -m "not integration and not docker"

quality:
	ruff check backend worker benchmarks tests
	mypy backend/app worker/app benchmarks
	$(PYTHON) -m compileall -q backend worker runtime benchmarks tests

integration:
	RUN_INTEGRATION_TESTS=1 $(PYTHON) -m pytest tests/integration/test_postgres_redis.py

docker-smoke: runtime-image
	RUN_DOCKER_TESTS=1 $(PYTHON) -m pytest tests/integration/test_runtime_container.py

benchmark:
	$(PYTHON) benchmarks/run_benchmark.py

runtime-image:
	docker build -t serverless-python311-runtime:latest -f runtime/python311/Dockerfile .

compose-up:
	docker compose up --build

compose-down:
	docker compose down
