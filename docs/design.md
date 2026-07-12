# Serverless Cloud Platform Design Document

## 1. Executive Summary

This project is a lightweight serverless cloud platform inspired by AWS Lambda.
It allows users to register versioned Python functions, invoke them through HTTP
APIs, execute each invocation inside a constrained Docker sandbox, and inspect
execution status, logs, and performance metrics through a dashboard.

The project is intentionally scoped as an educational infrastructure system, not
a production cloud provider. The goal is to demonstrate backend engineering,
distributed execution, queue reliability, container isolation, observability,
and performance analysis in a way that is strong enough for a software
engineering internship resume.

Core technologies:

- FastAPI for the API Gateway
- PostgreSQL for durable metadata and invocation state
- Redis Streams for reliable asynchronous invocation delivery
- Docker for sandboxed function execution
- React, TypeScript, and Recharts for the monitoring dashboard
- Docker Compose and GitHub Actions for local deployment and CI

## 2. Goals and Non-Goals

### Goals

- Register functions and immutable function versions.
- Invoke functions asynchronously through HTTP APIs.
- Execute user-defined code in isolated Docker containers.
- Track invocation lifecycle: queued, running, succeeded, failed, timed out,
  and retrying.
- Provide at-least-once task delivery with worker crash recovery.
- Enforce per-invocation CPU, memory, and timeout limits.
- Support retries with exponential backoff for infrastructure failures.
- Apply per-user and per-function rate limits.
- Expose operational metrics including queue latency, execution latency,
  success rate, error rate, worker health, and cold start count.
- Provide a benchmark suite with reproducible workload results.

### Non-Goals

- Production-grade multi-tenant security.
- Full AWS Lambda compatibility.
- Kubernetes-based orchestration in the MVP.
- Billing, quotas, regions, IAM, VPC integration, or cloud deployment.
- Multi-language runtime support in the MVP.
- Exactly-once execution semantics.

## 3. Design Principles

- Build a small but real cloud runtime instead of a CRUD application with Docker
  attached.
- Prefer correctness, fault recovery, and measurable behavior over a long list
  of shallow features.
- Make reliability semantics explicit: the platform guarantees at-least-once
  execution, not exactly-once execution.
- Keep the MVP implementable on one developer machine using Docker Compose.
- Treat Docker isolation honestly: useful for process and resource isolation,
  but not a complete security boundary against malicious tenants.
- Use benchmarks and failure-injection tests to prove the system works under
  concurrent workloads.

## 4. System Architecture

```text
                 +----------------------+
                 |      Web UI / CLI    |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 |     API Gateway      |
                 | FastAPI + Pydantic   |
                 +----------+-----------+
                            |
          +-----------------+-----------------+
          |                                   |
          v                                   v
+--------------------+              +-------------------+
| Function Registry  |              | Auth + Rate Limit |
| PostgreSQL         |              | JWT + Redis       |
+---------+----------+              +-------------------+
          |
          v
+--------------------+       +--------------------------+
| Invocation Records |<----->| Redis Streams Queue      |
| PostgreSQL         |       | Consumer Group + ACK     |
+---------+----------+       +-------------+------------+
          |                                |
          |                                v
          |                     +------------------------+
          |                     | Worker Pool            |
          |                     | Heartbeats + Leases    |
          |                     +-----------+------------+
          |                                 |
          |                                 v
          |                     +------------------------+
          |                     | Docker Runtime Sandbox |
          |                     | CPU/Memory/Timeout     |
          |                     +-----------+------------+
          |                                 |
          v                                 v
+--------------------+       +--------------------------+
| Results and Logs   |<------| Metrics Collector        |
| PostgreSQL / FS    |       | Dashboard API            |
+--------------------+       +--------------------------+
```

### Key Architecture Decision

The MVP should not use a separate centralized scheduler service. Workers should
pull tasks from Redis Streams using a consumer group. This keeps the system
simple while still demonstrating real distributed execution behavior:

- Redis Streams tracks pending messages.
- Workers acknowledge completed invocations.
- A recovery loop reclaims messages whose workers stopped heartbeating.
- Load balancing is naturally handled by multiple workers reading from the same
  consumer group.

A separate scheduler can be added later for priority scheduling or
resource-aware placement, but it is not required for the MVP.

## 5. Core Components

### 5.1 API Gateway

Responsibilities:

- Authenticate users with JWT.
- Validate function registration, version upload, and invocation payloads.
- Enforce rate limits before tasks enter the queue.
- Create durable invocation records in PostgreSQL.
- Publish invocation tasks to Redis Streams.
- Return invocation IDs immediately for asynchronous execution.
- Serve status, result, logs, and metrics APIs.

Example APIs:

```text
POST   /auth/register
POST   /auth/login
POST   /functions
GET    /functions
POST   /functions/{function_name}/versions
GET    /functions/{function_name}/versions
POST   /functions/{function_name}/invoke
GET    /invocations/{invocation_id}
GET    /invocations/{invocation_id}/logs
GET    /metrics/summary
GET    /workers
```

Invocation response:

```json
{
  "invocation_id": "uuid",
  "status": "QUEUED",
  "status_url": "/invocations/uuid"
}
```

### 5.2 Function Registry

The registry stores function metadata and immutable function versions.

Important rules:

- Function names are unique per user.
- A function version is immutable after creation.
- Runtime, handler, package hash, and resource limits belong to a version.
- The platform can support aliases such as `latest` or `prod` later, but the MVP
  can invoke the newest version by default.
- The source package is stored outside the database; PostgreSQL stores only the
  package URI/path and hash.

MVP package format:

```text
function.zip
+-- main.py
`-- requirements.txt   optional, phase 2
```

MVP runtime support:

- Python 3.11.
- Handler format: `module.function`, for example `main.handler`.
- Phase 1 supports standard-library Python functions.
- Phase 2 may add dependency installation from `requirements.txt` during a
  per-version build step.

### 5.3 Reliable Invocation Queue

The invocation queue should use Redis Streams rather than a simple Redis list.
This gives the project stronger reliability semantics and better resume value.

Queue behavior:

- The API creates an invocation row and an outbox row in the same PostgreSQL
  transaction with status `QUEUED`.
- A dispatcher publishes committed outbox rows to Redis Streams and records the
  resulting message ID. A crash after publish but before recording completion
  may publish a duplicate, which is valid under at-least-once semantics.
- Workers consume messages through a Redis consumer group.
- A worker acknowledges a message only after PostgreSQL stores either a
  terminal invocation state or a durable delayed outbox row for the next retry.
- If a worker crashes, the message remains pending and can be reclaimed.
- Worker rows persist their Redis consumer names. Recovery queries pending
  entries for only the stale consumer and transfers them with `XCLAIM`.

Delivery semantics:

- The system provides at-least-once delivery.
- A task may run more than once after a worker crash.
- Idempotency is supported with an optional client-provided idempotency key.
- Exactly-once execution is explicitly out of scope.

Recommended stream fields:

```text
invocation_id
function_version_id
owner_id
attempt_number
queued_at
deadline_at
```

### 5.4 Worker Pool

Workers are long-running processes that execute invocation tasks.

Responsibilities:

- Register themselves in PostgreSQL at startup.
- Send heartbeat updates every 5 seconds.
- Pull tasks from the Redis Streams consumer group.
- Mark invocations as `RUNNING`.
- Start Docker containers with configured resource limits.
- Capture stdout, stderr, exit code, result payload, and duration.
- Update invocation and attempt records transactionally.
- Acknowledge Redis messages only after database state is durable.

Worker states:

```text
IDLE
RUNNING
DRAINING
OFFLINE
```

Failure recovery:

- If a worker heartbeat is stale for more than 15 seconds, it is treated as
  unavailable.
- Pending Redis messages owned by that worker are reclaimed.
- Invocations that did not reach a terminal state are moved back to `QUEUED` or
  `RETRYING` if attempts remain.
- If attempts are exhausted, the invocation becomes `FAILED`.

### 5.5 Runtime Protocol

The runtime protocol defines how the platform executes user code inside a
container.

Container input:

- The worker launches the runtime container.
- The function package is mounted read-only.
- The invocation payload and context are passed to the runner as JSON.

Handler signature:

```text
handler(event, context) -> JSON-serializable value
```

Context fields:

```json
{
  "invocation_id": "uuid",
  "function_name": "hello",
  "function_version": "1",
  "deadline_ms": 30000,
  "memory_limit_mb": 256
}
```

Container output:

```json
{
  "ok": true,
  "result": {
    "message": "hello"
  }
}
```

Failure output:

```json
{
  "ok": false,
  "error_type": "ValueError",
  "error_message": "invalid input"
}
```

Runtime rules:

- stdout is reserved for the JSON result envelope.
- stderr is captured as logs.
- Non-zero exit code marks the attempt as failed.
- Invalid JSON output marks the invocation as failed.
- Payload, result, and log sizes are capped to protect the API and database.

### 5.6 Docker Sandbox

Each invocation runs inside a separate Docker container in the MVP.

Required limits:

- Memory limit, for example 256 MB.
- CPU quota, for example 0.5 CPU.
- Execution timeout.
- Process count limit.
- Read-only root filesystem.
- Temporary writable `/tmp`.
- Non-root container user.
- Dropped Linux capabilities.
- `no-new-privileges` security option.
- Network disabled by default.

Important security note:

Docker provides useful process, filesystem, and resource isolation for this
project, but it should not be presented as production-grade isolation against
malicious arbitrary code. The design should describe this as a local educational
runtime with a clearly documented threat model.

The platform must never mount the host Docker socket into user-function
containers.

### 5.7 Result and Log Storage

PostgreSQL stores invocation metadata and small result payloads.

Large outputs should be stored outside the invocation row:

- Local filesystem for the MVP.
- MinIO or object storage as a future improvement.

Recommended behavior:

- Store small JSON results inline.
- Store large results by reference.
- Store stderr logs by reference.
- Enforce maximum payload, result, and log sizes.

### 5.8 Authentication and Rate Limiting

MVP authentication:

- Email and password login.
- Passwords stored as hashes.
- JWT access tokens for API calls.

Rate limiting:

- Redis token bucket per user.
- Optional token bucket per function.
- Default limit: 100 invocations per minute per user.
- Return HTTP 429 before creating a queue task if the limit is exceeded.

API keys can be added later for CLI and programmatic invocation.

### 5.9 Monitoring Dashboard

The dashboard should focus on operational visibility, not decorative UI.

Required views:

- Function list with invocation count, success rate, and error rate.
- Invocation detail page with status, duration, result, error, and logs.
- Worker health page with heartbeat age, active tasks, and status.
- Metrics page with queue length, throughput, p50/p95/p99 latency, cold starts,
  retries, and timeouts.

Important latency metrics:

- Queue latency: worker start time minus queued time.
- Execution latency: completion time minus worker start time.
- End-to-end latency: completion time minus API invocation time.

## 6. Data Model

### users

```text
id
email
password_hash
created_at
```

Constraints:

- Unique email.

### functions

```text
id
owner_id
name
created_at
updated_at
deleted_at
```

Constraints:

- Unique `(owner_id, name)`.

### function_versions

```text
id
function_id
version_number
runtime
handler
package_uri
package_hash
memory_limit_mb
cpu_limit
timeout_seconds
created_at
```

Constraints:

- Unique `(function_id, version_number)`.
- Immutable after creation.

### invocations

```text
id
owner_id
function_version_id
idempotency_key
status
payload_ref
payload_inline
result_ref
result_inline
error_type
error_message
queued_at
started_at
completed_at
deadline_at
attempt_count
created_at
updated_at
```

Statuses:

```text
QUEUED
RUNNING
RETRYING
SUCCEEDED
FAILED
TIMEOUT
CANCELED
```

### invocation_attempts

```text
id
invocation_id
worker_id
attempt_number
status
container_id
exit_code
error_type
error_message
logs_ref
started_at
completed_at
duration_ms
```

### workers

```text
id
hostname
status
last_heartbeat
active_invocations
max_concurrency
started_at
updated_at
```

## 7. Invocation Lifecycle

### Successful Invocation

```text
1. Client calls POST /functions/{name}/invoke.
2. API authenticates the user and checks rate limits.
3. API resolves the requested function version.
4. API atomically creates an invocation and attempt-1 outbox row with status
   QUEUED.
5. Dispatcher publishes the committed outbox row to Redis Streams.
6. Worker reads the message from the consumer group.
7. Worker creates an invocation_attempt row.
8. Worker marks the invocation RUNNING.
9. Worker starts a Docker container with resource limits.
10. Runtime loads the function package and calls the handler.
11. Worker captures result, logs, exit code, and duration.
12. Worker stores result/log references.
13. Worker marks the invocation SUCCEEDED.
14. Worker acknowledges the Redis message.
```

### User-Code Failure

```text
1. Handler raises an exception or returns invalid output.
2. Worker captures error type, error message, and logs.
3. Invocation is marked FAILED.
4. User-code failures are not retried by default.
```

### Timeout

```text
1. Worker starts the container with a deadline.
2. If execution exceeds timeout_seconds, worker kills the container.
3. Invocation is marked TIMEOUT.
4. Timeout attempts are counted in invocation_attempts.
```

### Worker Crash

```text
1. Worker consumes a Redis message but crashes before ACK.
2. Heartbeat becomes stale.
3. Recovery logic reclaims the pending message.
4. Invocation is moved to RETRYING or QUEUED.
5. Another worker executes the task if attempts remain.
6. If max attempts is exceeded, invocation is marked FAILED.

Recovered messages carry their original attempt number. If a durable outbox row
already exists for the next attempt, the recovered older message is obsolete
and is acknowledged without execution. Otherwise it represents the crashed
attempt and may start the next attempt.
```

## 8. Retry Policy

Default policy:

- Maximum attempts: 3.
- Backoff: exponential backoff with jitter.
- Initial delay: 1 second.
- Maximum delay: 30 seconds.
- Queueing, backoff, and all attempts share the invocation's original deadline.

For retryable execution failures, the worker atomically stores the failed
attempt, moves the invocation to `RETRYING`, and creates an attempt-aware outbox
row with `available_at` set from the backoff decision. It then acknowledges the
old Redis message. The dispatcher publishes the next attempt only after that
timestamp.

Retryable failures:

- Worker crash.
- Container start failure.
- Infrastructure error.
- Redis or database transient error after the task was accepted.

Non-retryable failures:

- User-code exception.
- Invalid handler path.
- Invalid function package.
- Payload validation failure.
- Timeout, unless explicitly configured as retryable later.

This distinction is important because retrying deterministic user-code errors
wastes capacity and makes debugging harder.

## 9. Scheduling and Autoscaling

### MVP Scheduling

MVP scheduling is decentralized:

- Workers pull tasks from a shared Redis consumer group.
- Each worker advertises `max_concurrency`.
- A worker reads at most `max_concurrency` tasks per batch and processes them
  concurrently with a separate database session per task.
- FIFO ordering is best-effort because retries and multiple workers can reorder
  execution.

### Future Resource-Aware Scheduling

After the MVP, the platform can add a scheduler that considers:

- Requested memory.
- Requested CPU.
- Worker capacity.
- Queue age.
- Priority class.
- Historical execution duration.

This would be a strong extension for an Applied and Computational Mathematics
student because it connects scheduling decisions to queueing theory,
optimization, and performance modeling.

### Local Autoscaling

Autoscaling should be framed as a local simulation unless deployed to a real
orchestrator.

Recommended phase 3 behavior:

- An autoscaler process monitors queue depth, queue age, and active worker count.
- It increases or decreases the number of worker processes/containers within
  configured min and max bounds.
- It uses cooldown periods to avoid oscillation.
- The benchmark suite measures the impact on throughput and p95 latency.

Example policy:

```text
target_backlog_per_worker = 20
desired_workers = ceil(queue_depth / target_backlog_per_worker)
desired_workers = clamp(desired_workers, min_workers, max_workers)
```

## 10. Warm Starts

Cold start means a new container must be created for an invocation.

Warm start means an idle container for the same function version is reused.

Phase 3 warm-start design:

- Keep a small pool of idle containers per function version.
- Reuse warm containers only for the same function version.
- Evict idle containers after a timeout.
- Track cold starts, warm starts, and warm-start hit rate.

Important behavior:

- Global state inside a warm container may persist across invocations, similar
  to AWS Lambda.
- The documentation and sample functions should make this explicit.
- Warm start should be benchmarked separately from cold start.

## 11. Observability and Benchmarking

### Metrics

Function metrics:

- Invocation count.
- Success rate.
- Error rate.
- Timeout rate.
- Retry count.

Latency metrics:

- Average latency.
- p50 latency.
- p95 latency.
- p99 latency.
- Queue latency.
- Execution latency.
- End-to-end latency.

Infrastructure metrics:

- Queue depth.
- Oldest queued task age.
- Active workers.
- Active containers.
- Stale workers.
- Cold start count.
- Warm-start hit rate.

### Benchmark Suite

The benchmark suite should produce reproducible results for the README and
resume.

Required workloads:

- No-op function to measure platform overhead.
- CPU-bound function to test CPU limits and scheduling.
- Sleep function to test concurrency and timeout handling.
- Failing function to test error capture.
- Infinite loop function to test timeout enforcement.
- Memory-heavy function to test memory limits.

Required benchmark dimensions:

- Number of workers.
- Concurrent clients.
- Payload size.
- Cold start vs warm start.
- Retry and worker crash recovery.

Required benchmark output:

```text
throughput_invocations_per_second
p50_latency_ms
p95_latency_ms
p99_latency_ms
error_rate
timeout_rate
average_queue_latency_ms
average_execution_latency_ms
```

The final project should include a short benchmark report with hardware,
Docker version, workload configuration, and measured results.

## 12. Testing Strategy

Unit tests:

- API validation.
- Function version resolution.
- Rate limiter behavior.
- Retry policy decisions.
- Invocation state transitions.

Integration tests:

- API, PostgreSQL, and Redis working together.
- Function upload and invocation.
- Worker execution with Docker.
- Logs and results stored correctly.
- Redis message is acknowledged after successful database update.

Failure-injection tests:

- Kill a worker after it receives a task but before ACK.
- Run a function that exceeds timeout.
- Run a function that exceeds memory limit.
- Run a function with an invalid handler.
- Submit malformed payloads.
- Simulate stale worker heartbeat and task reclaim.

End-to-end tests:

- Register a user.
- Create a function.
- Upload a version.
- Invoke it concurrently.
- Poll until terminal status.
- Verify metrics and dashboard API values.

CI:

- Run unit tests on every push.
- Run integration tests with PostgreSQL and Redis service containers.
- Run Docker runtime tests when Docker is available in the CI environment.

## 13. Development Roadmap

### Phase 1: Reliable Core MVP, 2 to 3 weeks

Deliverables:

- FastAPI service with function and version APIs.
- PostgreSQL schema for users, functions, versions, invocations, attempts, and
  workers.
- Redis Streams invocation queue.
- Worker pool with heartbeat.
- Python 3.11 runtime protocol.
- Docker execution with CPU, memory, timeout, and basic sandbox options.
- Invocation status, result, and log retrieval.
- Basic integration tests.

Success criteria:

- A user can register a function, invoke it asynchronously, and retrieve the
  result.
- Multiple workers can consume from the same queue.
- A timed-out function is killed and marked `TIMEOUT`.
- A failed function returns useful error information and logs.

### Phase 2: Reliability, Auth, and Observability, 2 weeks

Deliverables:

- JWT authentication.
- Redis token-bucket rate limiting.
- Retry and backoff policy.
- Worker crash recovery through Redis pending-message reclaim.
- Dashboard APIs and basic React dashboard.
- Metrics for latency, throughput, retries, errors, and worker health.

Success criteria:

- Killing a worker mid-invocation does not permanently lose the task.
- Rate-limited requests return 429 before queue insertion.
- Dashboard shows real invocation and worker metrics.

### Phase 3: Performance Features and Resume-Ready Evidence, 2 to 3 weeks

Deliverables:

- Benchmark suite.
- Benchmark report.
- Warm-start container pool.
- Local autoscaling simulation.
- Optional resource-aware scheduling experiment.
- Polished README with architecture diagram, demo instructions, and measured
  performance results.

Success criteria:

- Benchmark report includes throughput, p95 latency, p99 latency, and error
  rate under concurrent workloads.
- Warm start reduces median latency for repeated invocations of the same
  function version.
- Autoscaling experiment shows the effect of worker count on queue latency.

## 14. Resume Positioning

This project is strongest when presented as a backend infrastructure and
distributed execution system.

Recommended resume bullet:

```text
Built a Lambda-inspired serverless execution platform with FastAPI,
PostgreSQL, Redis Streams, and Docker, supporting versioned function deployment,
asynchronous invocation, worker heartbeats, retry/backoff, timeout enforcement,
and container-level CPU/memory isolation.
```

Recommended quantified bullet after benchmarking:

```text
Benchmarked distributed worker pools under concurrent workloads, achieving
X invocations/sec with Y ms p95 latency; implemented Redis Streams ACK/reclaim
recovery to reassign tasks after worker failures.
```

For a University of Washington Applied and Computational Mathematics student,
the strongest narrative is:

- The project is not just a web service.
- It is a resource-constrained distributed execution system.
- It connects engineering implementation with queueing, scheduling,
  optimization, and performance measurement.
- It creates a credible bridge from computational mathematics to cloud
  infrastructure software engineering.

## 15. Risks and Mitigations

| Risk | Why It Matters | Mitigation |
| --- | --- | --- |
| Project becomes too broad | Too many features can lead to shallow implementation | Prioritize reliable queue, worker recovery, sandbox, and benchmark before UI polish |
| Docker security is overstated | Recruiters or engineers may notice weak threat modeling | Document Docker limits honestly and implement practical sandbox restrictions |
| Autoscaling is only theoretical | Resume claims need evidence | Implement local worker scaling simulation and benchmark it |
| Queue loses tasks on crash | Reliability is central to the project | Use Redis Streams consumer groups, ACK, pending reclaim, and durable invocation rows |
| Metrics are inaccurate | Dashboard loses credibility | Define queue, execution, and end-to-end latency precisely |
| No measurable results | Resume bullets become generic | Include benchmark report with concrete throughput and latency numbers |

## 16. Final MVP Definition

The minimum resume-worthy MVP is:

- Function registration and immutable version upload.
- Asynchronous invocation through FastAPI.
- Redis Streams task queue with at-least-once delivery.
- Multiple workers consuming from one consumer group.
- Docker-based Python runtime with CPU, memory, timeout, and basic security
  options.
- Durable invocation status, result, error, logs, and attempt history.
- Worker heartbeat and stale-worker recovery.
- Basic metrics and benchmark results.

If these features are implemented and demonstrated with tests and benchmark
data, the project is competitive for backend, cloud infrastructure, and
distributed systems oriented software engineering internship applications.
