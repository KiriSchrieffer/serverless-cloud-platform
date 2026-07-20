# Engineering Walkthrough

This document is a compact guide to the platform's architecture, reliability
semantics, failure handling, and release evidence. It is intended to make the
important engineering decisions easy to review without reading every service
implementation.

## Thirty-Second Overview

Serverless Cloud Platform is a local-first, Lambda-inspired execution system.
FastAPI accepts immutable Python function versions and asynchronous
invocations. PostgreSQL stores authoritative state and a transactional outbox,
Redis Streams delivers work to a consumer group, and workers execute each
invocation in a resource-limited Docker container. The system focuses on
durable dispatch, at-least-once processing, bounded retry, worker-crash
recovery, and observable end-to-end behavior rather than production-scale
multi-tenant isolation.

## End-to-End Invocation Path

1. A client authenticates and calls the invocation API with an optional
   idempotency key.
2. The API resolves an immutable function version and, in one PostgreSQL
   transaction, inserts both the `QUEUED` invocation and its first outbox row.
3. The dispatcher locks available outbox rows, publishes them to Redis Streams,
   and records the resulting stream message IDs.
4. A worker reads through a Redis consumer group, creates an attempt record,
   and marks the invocation `RUNNING`.
5. The worker starts a fresh Python 3.11 container with the package and input
   mounted read-only, network access disabled, Linux capabilities dropped, and
   CPU, memory, PID, timeout, and output limits applied.
6. The runtime loads the configured handler and returns a JSON protocol
   envelope while function logs are kept off the protocol stdout channel.
7. The worker persists the result, error, logs, and terminal database state
   before acknowledging the Redis message.

PostgreSQL remains the source of truth. Redis Streams is the delivery mechanism,
not the only record that an invocation exists.

## Why a Transactional Outbox

Writing an invocation to PostgreSQL and publishing directly to Redis are two
separate operations. Without an outbox, either ordering creates a failure
window:

- Commit first, then crash before publish: the invocation is durable but never
  reaches a worker.
- Publish first, then fail the database commit: a worker receives a task whose
  authoritative state does not exist.

The platform instead commits the invocation and dispatch intent atomically in
PostgreSQL. A separate dispatcher publishes only committed outbox rows. If it
crashes after publishing but before recording the Redis message ID, it may
publish again. That duplicate is accepted as part of the system's explicit
at-least-once contract and is handled by database state and attempt-aware
duplicate suppression.

## Delivery Semantics and Idempotency

The system promises at-least-once delivery, not exactly-once execution. A crash
can occur after user code runs but before the Redis ACK becomes durable, so an
execution may be observed again during recovery.

Two mechanisms limit duplicate effects inside the control plane:

- A client-supplied idempotency key is protected by a PostgreSQL uniqueness
  constraint scoped to the owner. Concurrent requests with the same key return
  the same invocation instead of creating two invocations.
- Invocation attempts and dispatches are uniquely identified by invocation and
  attempt number. Workers reject obsolete, duplicate, future, or already
  terminal tasks before executing them.

Application-level side effects performed by arbitrary user functions cannot be
made exactly once by this platform. A function that writes to an external
system must still use its own idempotency strategy.

## Consumer-Aware Worker Recovery

Each worker registers a stable database row and its Redis consumer name, then
sends heartbeats. Recovery treats a worker as stale after the configured
heartbeat threshold and performs four coordinated actions:

1. Lock and mark the stale worker offline.
2. Mark its running attempt as failed with `WorkerLostError`.
3. Move the invocation to `RETRYING`, or `FAILED` if attempts are exhausted.
4. Query Redis pending entries for that exact stale consumer and transfer them
   with `XCLAIM`.

The consumer-specific query is important. Reclaiming every sufficiently old
pending message could steal a valid long-running invocation from a healthy
worker. Mapping database heartbeats to Redis consumer ownership makes recovery
target the failed worker instead.

Recovered messages retain their attempt number. If a durable outbox row already
exists for a later attempt, the older message is acknowledged as obsolete
without running the function again.

## Retry and Deadline Policy

Infrastructure failures are retried up to three total attempts with exponential
backoff, jitter, and a 30-second delay cap. User-code errors, invalid packages,
invalid output, memory-limit failures, and timeouts are not retried by default
because they are normally deterministic.

Queueing, backoff, and execution all consume the invocation's original
end-to-end deadline. A task that reaches a worker after that deadline is marked
`TIMEOUT` without starting another container. This prevents retries from
silently extending the latency contract.

## Timeout Failure Case Study

An early Compose test exposed a platform-specific timeout classification bug.
The same function that correctly timed out locally was marked `FAILED` on a
GitHub Actions runner.

The Docker SDK did not always raise Python's built-in `TimeoutError` directly.
Depending on its HTTP transport, it could raise a requests or urllib3 exception
with the real read-timeout nested in `__cause__`, `__context__`, or exception
arguments. The generic worker error path therefore converted a valid timeout
into a failure, which also changed the aggregate metrics counts.

The fix recognizes the wrapped timeout chain, kills the runtime container,
preserves available logs, and returns the original `TIMEOUT` outcome even if
cleanup subsequently fails. Unit regressions cover wrapped transport errors and
cleanup failures, while the Compose workflow verifies the observable API state.

The broader lesson is that infrastructure exceptions should be classified at
the boundary where transport-specific errors enter the domain model. Cleanup
must not overwrite the primary execution result.

## Release Benchmark: What It Proves

The v1.0.0 release suite ran three independent repetitions of each workload:

| Scenario | Invocations per run | Clients | Median throughput | Median p95 latency |
| --- | ---: | ---: | ---: | ---: |
| No-op | 100 | 10 | 2.44/s | 6617.83 ms |
| Sleep 200 ms | 100 | 10 | 1.96/s | 7407.67 ms |
| CPU-bound | 50 | 5 | 1.53/s | 4583.24 ms |

Across all nine runs, 750 of 750 invocations reached `SUCCEEDED`. Every sample
traversed the real API, PostgreSQL outbox, Redis Stream, worker, and Docker
runtime path.

This is correctness and reproducibility evidence, not a production throughput
claim. The measured topology was one worker with total concurrency two, every
invocation created a cold container, and the reported latency includes queue
time. Under that topology, queueing and container startup dominate the tail.
The raw JSON files, host metadata, runtime image digest, commit SHA, and median
aggregation are retained under `benchmarks/results/release/` and
`docs/benchmark-release-report.md`.

## Verification Strategy

The repository currently contains 128 collected tests across four layers:

- Fast unit and failure-injection tests for API, outbox, worker, retry,
  recovery, runtime protocol, rate limiting, metrics, and benchmark logic.
- Real PostgreSQL and Redis concurrency tests for idempotency and the atomic
  token bucket.
- A real runtime-image test for the container protocol.
- Full Docker Compose workflows for success, handler error, timeout, invalid
  output, memory limit, worker crash recovery, logs, and metrics.

GitHub Actions additionally runs ruff, mypy, compile checks, Alembic upgrade and
drift checks, the frontend build, image builds, and the Compose stack.

## Explicit Limits

- Docker restrictions reduce risk but are not a security boundary against
  hostile multi-tenant code.
- Every invocation is cold; there is no warm-container pool.
- Workers use bounded concurrency. The release benchmark remains a single-worker
  correctness baseline; the separate scaling experiment is exploratory and
  single-host only.
- The project is local-first and does not claim a production AWS or Kubernetes
  deployment.
- Exactly-once execution, API keys, refresh tokens, password recovery, and
  production secret management are outside the v1.0.0 scope.

## Worker Scaling Experiment

A follow-up experiment measured one, two, and four workers with fresh database
and queue state per topology, a 20-invocation warm-up, and three measured
100-invocation no-op runs. All 900 measured invocations succeeded. Median
throughput was 2.95/s, 3.03/s, and 6.17/s respectively, so four workers achieved
a 2.09x speedup over one worker rather than linear 4x scaling.

The result also exposed substantial host-level variance: throughput CV reached
43.56% at two workers, and later runs showed higher container execution latency
without retries, worker errors, or leaked containers. This verifies parallel
consumer behavior and a capacity increase at four workers, but it is not strong
enough for a production scaling claim or a new resume metric. The complete
methodology and raw evidence are in `docs/benchmark-worker-scaling-report.md`
and `benchmarks/results/scaling/20260720-114614/`.

A future warm-container experiment could isolate Docker startup cost from
worker scheduling. A controlled multi-host environment would be required for a
stronger horizontal-scaling claim.
