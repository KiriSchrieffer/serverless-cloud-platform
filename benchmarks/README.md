# Benchmarks

This directory contains local benchmark workloads and a runner for measuring the
API -> Redis Streams -> worker -> Docker runtime path.

Start the full local platform first:

```bash
docker compose up --build
```

Then run a benchmark from another terminal:

```bash
python3 benchmarks/run_benchmark.py \
  --workload noop \
  --invocations 20 \
  --concurrency 5
```

The runner will:

- package `benchmarks/workloads/<workload>/main.py` as `function.zip`
- create or reuse a benchmark function
- upload a new function version
- invoke the function concurrently
- poll each invocation until terminal status
- write `docs/benchmark-report.md`
- write raw JSON to `benchmarks/results/latest.json`

Useful workload examples:

```bash
python3 benchmarks/run_benchmark.py --workload sleep \
  --payload '{"seconds":0.2}' --invocations 20 --concurrency 5

python3 benchmarks/run_benchmark.py --workload cpu_bound \
  --payload '{"n":250000}' --invocations 20 --concurrency 5

python3 benchmarks/run_benchmark.py --workload failing \
  --invocations 10 --concurrency 2

python3 benchmarks/run_benchmark.py --workload infinite_loop \
  --timeout-seconds 2 --poll-timeout-seconds 30 --invocations 5 --concurrency 1
```

Failure-injection evidence is covered by:

```bash
python3 -m pytest tests/failure_injection/test_worker_crash_recovery.py
```

That regression models a worker crash after Redis Streams delivery but before ACK,
then verifies a later worker can reclaim and complete the pending task.
