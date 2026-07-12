# Worker

The worker consumes invocation messages from Redis Streams, updates durable
invocation state in PostgreSQL, executes user code inside Docker containers,
and processes up to its configured concurrency with one database session per
task. It acknowledges a message only after a terminal state or a durable
delayed retry outbox row is stored.

Each worker persists its Redis consumer name with its heartbeat record. Crash
recovery uses `XPENDING` plus `XCLAIM` for those exact stale consumers, so a
healthy worker's long-running pending messages are not transferred. Duplicate
or obsolete attempt messages are acknowledged without re-execution, and every
attempt is bounded by the invocation's original deadline.
