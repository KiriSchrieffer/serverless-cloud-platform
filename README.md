# Serverless Cloud Platform

Lightweight educational serverless platform inspired by AWS Lambda.

This repository is organized around the architecture in
`docs/design.md`: a FastAPI API gateway, PostgreSQL metadata store,
Redis Streams invocation queue, Docker-based Python runtime, worker pool,
React monitoring dashboard, and benchmark suite.

## Repository Layout

```text
backend/       FastAPI gateway, schemas, services, database migrations
worker/        Long-running worker process, heartbeats, recovery, Docker execution
runtime/       In-container Python runtime runner and runtime image
frontend/      React and TypeScript monitoring dashboard
infra/         Local infrastructure notes and service configuration
scripts/       Developer and operations helper scripts
tests/         Unit, integration, failure-injection, and end-to-end tests
benchmarks/    Reproducible workload drivers and sample functions
examples/      Example user functions and metadata
storage/       Local package, result, and log storage for MVP development
docs/          Design notes, threat model, benchmark report template
```

The current files are a scaffold, not a complete implementation.
