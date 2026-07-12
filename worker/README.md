# Worker

The worker consumes invocation messages from Redis Streams, updates durable
invocation state in PostgreSQL, executes user code inside Docker containers,
and processes up to its configured concurrency with one database session per
task. It acknowledges a message only after a terminal state or a durable
delayed retry outbox row is stored.
