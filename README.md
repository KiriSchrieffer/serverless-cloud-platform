# Serverless Cloud Platform

A lightweight Lambda-style serverless platform for local development and systems
experiments. The project demonstrates function registration, immutable version
uploads, asynchronous invocation dispatch through Redis Streams, worker
heartbeats, Docker-based Python runtime execution, retry/recovery behavior,
JWT authentication, Redis rate limiting, metrics, logs, and benchmark evidence.

## What It Does

- Registers functions and uploads zipped Python handler packages.
- Creates immutable function versions with runtime limits and package hashes.
- Accepts async invocations through `POST /functions/{name}/invoke`.
- Commits invocations and dispatch records atomically through a transactional
  outbox, then publishes work to Redis Streams.
- Executes user code in a Docker runtime from a worker process.
- Stores terminal invocation status, result, error, and logs.
- Recovers stale workers and reclaims pending Redis Stream messages.
- Reclaims messages by stale consumer identity without stealing healthy
  workers' long-running tasks.
- Runs bounded concurrent invocations per worker and schedules retryable
  failures through delayed outbox records with exponential backoff and jitter.
- Deduplicates attempt deliveries and enforces one total invocation deadline
  across queueing, retries, and Docker execution.
- Authenticates API users with bcrypt password hashes and signed JWT access
  tokens; owner identity never comes from a caller-supplied owner header.
- Applies an atomic Redis token bucket before invocation records enter the
  transactional outbox, with a default limit of 100 invocations per minute.
- Exposes worker health and invocation metrics APIs.
- Reports queue depth and age, pending dispatches, retries, trailing-minute
  throughput, terminal error rate, and end-to-end p50/p95/p99 latency.
- Provides an authenticated Dashboard workflow for function creation, ZIP
  upload, invocation, result/log inspection, and operational refresh.
- Provides local benchmark workloads and a reproducible benchmark report.

## Architecture

```mermaid
flowchart LR
    Client["Client or benchmark runner"] --> API["FastAPI API"]
    Browser["Dashboard on :3000"] --> Frontend["Nginx + React"]
    Frontend --> API
    API --> DB["PostgreSQL metadata + outbox"]
    DB --> Dispatcher["Outbox dispatcher"]
    Dispatcher --> Queue["Redis Streams"]
    Queue --> Worker["Worker process"]
    Worker --> Runtime["Docker Python runtime"]
    Runtime --> Worker
    Worker --> DB
    Worker --> Storage["Local storage: packages, logs, results"]
    Client --> Metrics["Workers and metrics APIs"]
    Metrics --> DB
```

The platform is intentionally local-first. It is useful for demonstrating
serverless control-plane and worker-runtime concepts, not for production-grade
multi-tenant isolation.

## Repository Layout

```text
backend/       FastAPI gateway, schemas, services, database migrations
worker/        Worker loop, heartbeats, Redis consumer, recovery, Docker execution
runtime/       In-container Python runtime runner and runtime image
frontend/      React and TypeScript monitoring dashboard
infra/         Local infrastructure notes
scripts/       Demo and developer helper scripts
tests/         Unit and failure-injection tests
benchmarks/    Benchmark runner and workload functions
examples/      Example user functions
storage/       Local package, result, and log storage
docs/          Design notes, threat model, benchmark report
```

## Local Quick Start

Requirements:

- Docker Desktop or Docker Engine
- Python 3.11+
- Bash and curl

Start the full local platform:

```bash
git clone https://github.com/KiriSchrieffer/serverless-cloud-platform.git
cd serverless-cloud-platform

docker compose up --build
```

This starts:

- PostgreSQL
- Redis
- a one-shot Alembic database migration
- the Python 3.11 runtime image build
- FastAPI API on `http://localhost:8000`
- React Dashboard on `http://localhost:3000`
- transactional outbox dispatcher
- Worker process connected to Redis Streams

The compose setup uses `.env.example` for local defaults. PostgreSQL and Redis
must pass their health checks, and the migration must complete, before the API
and worker start. The Dashboard waits for the API health check and proxies
browser requests from `/api` to FastAPI, so no separate frontend command is
required for the full Compose stack.

Open `http://localhost:3000`, register a local account, then use the three-step
Deploy & Invoke panel to create a function, upload its ZIP package, and invoke
it. Accepted invocations automatically open in the detail panel for refresh,
result, error, and log inspection.

## Run the Demo Invocation

In a second terminal, run this from the repository root:

```bash
bash scripts/demo_invoke.sh
```

The script will:

- create a sample function
- build a `function.zip`
- upload a new version
- invoke the function
- poll until terminal status
- print the invocation response and logs

Expected terminal state is `SUCCEEDED`, with a result similar to:

```json
{"message":"hello Ada"}
```

## Useful API Calls

Register and obtain a local access token:

```bash
curl -fsS -X POST http://localhost:8000/auth/register -H "Content-Type: application/json" -d '{"email":"demo@example.local","password":"local-demo-password"}'
```

```bash
export ACCESS_TOKEN="$(curl -fsS -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" -d '{"email":"demo@example.local","password":"local-demo-password"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
```

Create a function:

```bash
curl -fsS -X POST http://localhost:8000/functions \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hello"}'
```

Invoke a function:

```bash
curl -fsS -X POST http://localhost:8000/functions/hello/invoke \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"name":"Ada"},"idempotency_key":"demo-hello"}'
```

Query an invocation:

```bash
curl -fsS -H "Authorization: Bearer $ACCESS_TOKEN" http://localhost:8000/invocations/<invocation_id>
```

Query invocation logs:

```bash
curl -fsS -H "Authorization: Bearer $ACCESS_TOKEN" http://localhost:8000/invocations/<invocation_id>/logs
```

Query workers and metrics:

```bash
curl -fsS -H "Authorization: Bearer $ACCESS_TOKEN" http://localhost:8000/workers
curl -fsS -H "Authorization: Bearer $ACCESS_TOKEN" http://localhost:8000/metrics/summary
```

## Benchmarks

Run a real local benchmark after `docker compose up --build` is running:

```bash
python3 benchmarks/run_benchmark.py \
  --workload noop \
  --invocations 100 \
  --concurrency 10
```

The benchmark runner registers and logs in with local defaults. Override them
with `BENCHMARK_EMAIL` and `BENCHMARK_PASSWORD`; credentials are never written
to benchmark JSON or Markdown reports.

Earlier single-run local Docker Compose results (useful for development, but not
final resume evidence):

| Workload | Invocations | Concurrency | Success rate | Throughput | p95 latency | Avg queue latency | Avg execution latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| noop | 100 | 10 | 100% | 4.22/sec | 2575.47 ms | 1899.81 ms | 216.72 ms |
| sleep 0.2s | 100 | 10 | 100% | 2.26/sec | 4543.32 ms | 3673.18 ms | 425.3 ms |

The reports are in `docs/benchmark-noop-100x10.md` and
`docs/benchmark-sleep-100x10.md`. Raw JSON is stored in
`benchmarks/results/noop-100x10.json` and `benchmarks/results/sleep-100x10.json`.

For release-candidate evidence, start from a clean, committed worktree and run:

```bash
make release-benchmark
```

The release suite refuses a dirty worktree, records the commit, host, Docker and
runtime-image metadata, runs no-op, sleep, and CPU-bound scenarios three times
each, preserves every raw JSON run, and generates median results in
`docs/benchmark-release-report.md`. Small samples cannot overwrite the tracked
default benchmark report; use explicit temporary output paths for smoke runs.

Additional workloads:

```bash
python3 benchmarks/run_benchmark.py --workload sleep \
  --payload '{"seconds":0.2}' \
  --invocations 20 \
  --concurrency 5

python3 benchmarks/run_benchmark.py --workload cpu_bound \
  --payload '{"n":250000}' \
  --invocations 20 \
  --concurrency 5

python3 benchmarks/run_benchmark.py --workload failing \
  --invocations 10 \
  --concurrency 2
```

## Recovery and Failure Injection

The worker uses Redis Streams consumer groups and supports pending message
reclaim with at-least-once execution semantics. If a worker crashes after
receiving a task but before ACK, a later worker can reclaim the pending message,
mark the stale worker offline, record the lost attempt, and continue processing.

Run the failure-injection regression:

```bash
python3 -m pytest tests/failure_injection/test_worker_crash_recovery.py
```

## Development Checks

Install local test dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test,worker,dev]"
```

Run the test suite:

```bash
python3 -m compileall backend worker benchmarks tests
.venv/bin/python -m pytest
ruff check backend worker benchmarks tests
mypy backend/app worker/app benchmarks
git diff --check
```

Real service checks are intentionally opt-in. With PostgreSQL and Redis running
and migrations applied:

```bash
RUN_INTEGRATION_TESTS=1 .venv/bin/python -m pytest tests/integration/test_postgres_redis.py
```

With Docker running and the runtime image built:

```bash
make docker-smoke
```

With the complete Compose stack running:

```bash
make e2e
```

Current test coverage includes:

- API health and function registry behavior
- registration, password hashing, JWT validation, user isolation, and rate limits
- real PostgreSQL concurrent idempotency and Redis atomic token-bucket checks
- real runtime-image stdout protocol smoke test
- complete Compose success, handler-error, timeout, memory-limit, invalid-output,
  worker-crash recovery, log, and metrics workflows
- package upload and invocation creation
- Redis Streams producer/consumer parsing
- Docker runtime executor behavior
- worker heartbeat, retry policy, and stale worker recovery
- workers and metrics APIs
- benchmark runner calculations
- worker crash failure injection

## Current Limits

- The access token is stored in browser session storage for this local MVP;
  production deployment would use a hardened cookie/session strategy and secret management.
- Docker runtime isolation is not production-grade sandboxing.
- Autoscaling and Kubernetes scheduling are out of scope for this MVP.
- API keys, refresh tokens, password reset, and account administration are not implemented.
- Real PostgreSQL, Redis, and runtime-container tests require their external
  services and therefore skip in the default fast unit-test command.
