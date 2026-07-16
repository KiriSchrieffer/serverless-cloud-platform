# Tests

Test layout:

- `unit/` for validation, rate limits, retry policy, and state transitions
- `integration/` for opt-in real PostgreSQL, Redis, and Docker runtime checks
- `failure_injection/` for the deterministic worker-crash recovery model
- `e2e/` for black-box API-to-worker-to-Docker workflows, timeout and OOM
  enforcement, real worker crash recovery, and metrics checks

The default test command excludes checks that require external services. CI and
Docker-enabled development environments enable them explicitly with
`RUN_INTEGRATION_TESTS=1` or `RUN_DOCKER_TESTS=1`.
The complete Compose suite uses `RUN_E2E_TESTS=1`, expects ports 8000 and 3000
to expose the API and Dashboard, and restarts the Compose worker during its
process-crash scenario.
