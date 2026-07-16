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

Runs with fewer than 20 invocations must use explicit `--report-path` and
`--json-output-path` arguments, so a one-request smoke test cannot overwrite
tracked performance evidence.

## Release-candidate suite

After committing all code and starting a clean Compose stack, run:

```bash
python3 -m benchmarks.run_release_suite
```

The suite requires a clean Git worktree and a live Docker server, runtime image,
and worker. It captures the commit SHA, CPU/RAM, operating system, Docker and
image identifiers, and worker topology before it writes output. It runs the
no-op 100×10, sleep-200ms 100×10, and CPU-bound 50×5 scenarios three times,
stores every raw run under `benchmarks/results/release/<timestamp>/`, and writes
an aggregate median report to `docs/benchmark-release-report.md`.

The audited release evidence for commit
`eb421c01eaaf110e7d24f7690284e1556296a7ca` is stored under
`benchmarks/results/release/20260716-013335/`. Its nine raw runs contain 750
successful cold-container invocations; `aggregate.json` and
`docs/benchmark-release-report.md` contain the corresponding three-run medians
and reproducibility metadata.

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
