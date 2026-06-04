# Worker

The worker consumes invocation messages from Redis Streams, updates durable
invocation state in PostgreSQL, executes user code inside Docker containers,
and acknowledges messages only after terminal state is stored.
