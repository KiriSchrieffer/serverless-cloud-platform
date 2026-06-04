# Tests

Planned test layout:

- `unit/` for validation, rate limits, retry policy, and state transitions
- `integration/` for API, PostgreSQL, Redis, and Docker runtime behavior
- `failure_injection/` for worker crash, timeout, memory, and invalid handler cases
- `e2e/` for user registration through concurrent invocation and metrics checks
