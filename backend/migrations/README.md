# Database Migrations

Alembic migration files live here.

Run migrations after PostgreSQL is available:

```bash
alembic upgrade head
```

Initial schema targets:

- users
- functions
- function_versions
- invocations
- invocation_attempts
- workers
