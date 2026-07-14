# Tests

Test layout:

- `unit/` for validation, rate limits, retry policy, and state transitions
- `integration/` for opt-in real PostgreSQL, Redis, and Docker runtime checks
- `failure_injection/` for worker crash, timeout, memory, and invalid handler cases
- `e2e/` for user registration through concurrent invocation and metrics checks

The default test command excludes checks that require external services. CI and
Docker-enabled development environments enable them explicitly with
`RUN_INTEGRATION_TESTS=1` or `RUN_DOCKER_TESTS=1`.
