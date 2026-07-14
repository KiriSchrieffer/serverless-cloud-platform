# Frontend

React and TypeScript dashboard for authenticated function health, invocation
details, worker status, and platform metrics. The local MVP supports account
registration, login, session-scoped Bearer tokens, and logout.

The Dashboard also provides the complete primary workflow without curl:

- Create a function.
- Upload a Python 3.11 ZIP version with handler and resource limits.
- Invoke a selected version with a JSON payload and optional idempotency key.
- Open and refresh invocation state, result, errors, and logs.
- Inspect queue, retry, throughput, latency, worker, and status metrics.

`docker compose up --build` serves the production build through Nginx at
`http://localhost:3000`. For frontend development, `npm run dev` continues to
serve Vite at `http://localhost:5173` with `/api` proxied to the local API.
