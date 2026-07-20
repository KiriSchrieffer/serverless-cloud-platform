PYTHON ?= python3
PIP_TOOLS_VERSION ?= 7.6.0

.PHONY: api worker frontend test quality integration docker-smoke e2e benchmark release-benchmark dependency-lock runtime-image compose-up compose-down

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

e2e:
	RUN_E2E_TESTS=1 $(PYTHON) -m pytest tests/e2e/test_compose_workflows.py

benchmark:
	$(PYTHON) benchmarks/run_benchmark.py

release-benchmark:
	$(PYTHON) -m benchmarks.run_release_suite

dependency-lock:
	docker run --rm --user "$(shell id -u):$(shell id -g)" --env HOME=/tmp -v "$(CURDIR):/app" -w /app python:3.11-slim sh -c 'python -m pip install --quiet pip-tools==$(PIP_TOOLS_VERSION) && python -m piptools compile --strip-extras --extra=test --extra=worker --extra=dev --output-file=requirements/constraints.txt pyproject.toml'

runtime-image:
	docker build -t serverless-python311-runtime:latest -f runtime/python311/Dockerfile .

compose-up:
	docker compose up --build

compose-down:
	docker compose down
