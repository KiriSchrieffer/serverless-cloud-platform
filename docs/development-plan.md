# Development Plan

This plan defines the resume-ready MVP for the Serverless Cloud Platform. A
milestone is complete only when its implementation, automated checks, and
documented verification evidence are all present.

## Current Status

- Milestone 0 implementation is complete; clean Docker startup and demo
  verification remain pending until Docker is available.
- Milestone 1 implementation and unit coverage are complete; real PostgreSQL
  concurrent-idempotency verification belongs to Milestone 2.
- Milestone 3 now has bounded worker concurrency, row-locked state changes,
  attempt uniqueness, delayed exponential-backoff retries, total deadline
  enforcement, duplicate-attempt suppression, and exact stale-consumer reclaim.
- Milestone 4 implementation and unit coverage are complete: bcrypt registration,
  JWT login, token-derived ownership, cross-user isolation, an atomic Redis
  invocation token bucket, and dashboard authentication are present. Real Redis
  verification remains part of Milestone 2 and release-candidate testing.
- Milestone 5 implementation is present: expanded operational metrics, a
  no-curl Dashboard workflow, a Compose-served frontend, opt-in real service
  tests, and CI gates for lint, typing, migrations, integration, frontend, and
  Docker runtime smoke. GitHub CI execution and final Docker-enabled validation
  remain pending.
- Milestone 2 now has executable real PostgreSQL/Redis tests plus black-box
  Compose workflows for success, handler failure, timeout, invalid runtime
  output, log retrieval, metrics, and recovery after a real worker process is
  killed. Docker-enabled CI is the remaining validation gate.

## Delivery Principles

- Keep the platform local-first and honest about Docker isolation.
- Preserve at-least-once delivery semantics and make duplicate execution safe.
- Add regression coverage with every correctness fix.
- Keep smoke-test output separate from versioned benchmark evidence.
- Run final performance tests only from a clean, pinned release candidate.

## Milestone 0: Reproducible Local Bootstrap

Deliverables:

- Docker Compose builds the Python runtime image.
- PostgreSQL and Redis expose health checks.
- Alembic migrations run once before the API and worker start.
- The API exposes a container health check.
- User-function stdout is captured as logs without corrupting the runtime JSON
  result envelope.

Exit criteria:

- `docker compose up --build` works with empty volumes.
- `bash scripts/demo_invoke.sh` reaches `SUCCEEDED` and returns logs.
- Unit tests, frontend build, migration smoke test, and Compose validation pass.

## Milestone 1: Durable Dispatch and Idempotency

Deliverables:

- Remove the PostgreSQL-commit/Redis-publish race with a transactional outbox or
  equivalent durable dispatcher.
- Add a unique owner/idempotency-key constraint for non-null keys.
- Make repeated requests return the original invocation safely under
  concurrency.
- Record dispatch state and make failed publishes recoverable.

Exit criteria:

- Concurrent duplicate requests create exactly one invocation.
- A worker cannot observe a queue message before its invocation is committed.
- Redis unavailability does not silently lose an accepted invocation.

## Milestone 2: Real Integration and Failure Tests

Deliverables:

- Tests backed by real PostgreSQL and Redis.
- Docker runtime tests using the real runtime image.
- End-to-end tests for success, handler error, timeout, invalid output, and log
  retrieval.
- Process-level worker crash and pending-message recovery test.

Exit criteria:

- The complete API-to-container path runs in CI or a documented Docker-enabled
  test job.
- Failure tests verify durable state and Redis acknowledgement behavior.

## Milestone 3: Worker Concurrency and Retry Scheduling

Deliverables:

- Enforce `max_concurrency` with bounded parallel execution.
- Reclaim only tasks that are safe to recover.
- Add exponential backoff with jitter for retryable infrastructure failures.
- Enforce invocation deadlines across retries.
- Add attempt-level uniqueness and race-safe state transitions.

Exit criteria:

- Active work never exceeds configured concurrency.
- Long-running healthy invocations are not reclaimed as lost work.
- Retry timing and attempt exhaustion are covered by deterministic tests.

## Milestone 4: Authentication and Rate Limiting

Deliverables:

- User registration, password hashing, login, and JWT access tokens.
- Owner identity derived from the token instead of `X-Owner-Id`.
- Redis token buckets per user and optionally per function.
- Clear 401, 403, and 429 behavior.

Exit criteria:

- Cross-user function and invocation access is rejected.
- Rate-limited requests do not create database or queue records.

## Milestone 5: Observability, Dashboard, and CI

Deliverables:

- Queue depth, queue age, retry count, throughput, error rate, and latency
  percentiles.
- Dashboard flows for upload, invoke, inspect, and refresh.
- Frontend served or documented as part of the full local stack.
- Ruff, type checking, frontend build, migrations, unit tests, integration
  tests, and Docker smoke tests in CI.

Exit criteria:

- A user can complete the primary workflow without raw curl commands.
- Dashboard values agree with database and queue state.

## Milestone 6: Performance Features

Warm starts and local autoscaling are stretch goals. Implement them only after
the reliable MVP above is complete.

Possible deliverables:

- Per-version warm container pool with idle eviction.
- Cold/warm start counters and benchmark comparison.
- Local worker autoscaling based on queue depth and oldest-message age.

## Milestone 7: Release-Candidate Validation

Final validation must run against a pinned commit from clean database, Redis,
storage, and Compose state.

Required evidence:

- Commit SHA, host CPU/RAM, operating system, Docker version, image identifiers,
  worker count, concurrency, and cold/warm conditions.
- At least three repeated runs per performance scenario.
- Versioned raw JSON for every run and a generated aggregate report.
- No use of a one-request smoke run as performance evidence.

Suggested scenarios:

- No-op: 100 invocations at concurrency 10.
- Sleep 200 ms: 100 invocations at concurrency 10.
- CPU-bound: 50 invocations at concurrency 5.
- Timeout, memory-limit, handler-failure, and worker-crash correctness tests.
- Worker-count comparison when concurrency support is complete.

Resume metrics should use the median result across repeated runs and state the
workload and concurrency explicitly.
