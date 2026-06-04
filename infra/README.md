# Infrastructure

Local infrastructure is driven by `docker-compose.yml`.

MVP services:

- PostgreSQL for metadata and invocation state
- Redis Streams for invocation delivery and rate limiting
- API gateway container
- Worker container
