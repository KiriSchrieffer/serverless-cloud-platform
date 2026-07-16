#!/usr/bin/env python3
"""Run repeated release-candidate benchmarks and aggregate median results."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarks.run_benchmark import (
    ApiClient,
    BenchmarkConfig,
    BenchmarkReport,
    ROOT_DIR,
    collect_environment,
    positive_float,
    positive_int,
    report_to_dict,
    run_benchmark,
    utc_now_iso,
)

DEFAULT_REPORT_PATH = ROOT_DIR / "docs" / "benchmark-release-report.md"
AGGREGATE_METRICS = (
    "throughput_invocations_per_second",
    "success_rate",
    "error_rate",
    "timeout_rate",
    "p50_latency_ms",
    "p95_latency_ms",
    "p99_latency_ms",
    "average_queue_latency_ms",
    "average_execution_latency_ms",
    "average_accept_latency_ms",
)


@dataclass(frozen=True)
class BenchmarkScenario:
    name: str
    workload: str
    invocations: int
    concurrency: int
    payload: Any
    timeout_seconds: int = 30
    memory_limit_mb: int = 256
    cpu_limit: float = 0.5


RELEASE_SCENARIOS = (
    BenchmarkScenario("noop-100x10", "noop", 100, 10, {}),
    BenchmarkScenario("sleep-200ms-100x10", "sleep", 100, 10, {"seconds": 0.2}),
    BenchmarkScenario("cpu-bound-50x5", "cpu_bound", 50, 5, {"n": 250000}),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated benchmarks from a clean release-candidate commit."
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--repetitions", type=positive_int, default=3)
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=[scenario.name for scenario in RELEASE_SCENARIOS],
        default=[scenario.name for scenario in RELEASE_SCENARIOS],
    )
    parser.add_argument("--results-dir", type=Path)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--poll-interval-seconds", type=positive_float, default=0.25)
    parser.add_argument("--poll-timeout-seconds", type=positive_float, default=180.0)
    parser.add_argument("--http-timeout-seconds", type=positive_float, default=15.0)
    parser.add_argument(
        "--auth-password",
        default=os.getenv("BENCHMARK_PASSWORD", "local-benchmark-password"),
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow development runs from a dirty worktree; never use for resume evidence",
    )
    return parser.parse_args()


def scenario_config(
    scenario: BenchmarkScenario,
    *,
    api_url: str,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
    http_timeout_seconds: float,
) -> BenchmarkConfig:
    return BenchmarkConfig(
        api_url=api_url,
        workload=scenario.workload,
        function_name=f"bench-release-{scenario.workload}",
        invocations=scenario.invocations,
        concurrency=scenario.concurrency,
        payload=scenario.payload,
        timeout_seconds=scenario.timeout_seconds,
        memory_limit_mb=scenario.memory_limit_mb,
        cpu_limit=scenario.cpu_limit,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        http_timeout_seconds=http_timeout_seconds,
    )


def collect_release_environment(
    *,
    api_url: str,
    auth_password: str,
    http_timeout_seconds: float,
    suite_id: str,
) -> dict[str, Any]:
    api = ApiClient(api_url, timeout_seconds=http_timeout_seconds)
    api.authenticate(f"benchmark-release-{suite_id}-preflight@example.local", auth_password)
    return collect_environment(api)


def validate_release_environment(
    environment: dict[str, Any],
    *,
    allow_dirty: bool,
) -> None:
    errors: list[str] = []
    if environment.get("git_commit_sha") in {None, "unknown"}:
        errors.append("Git commit SHA is unavailable")
    if environment.get("git_worktree_clean") is not True and not allow_dirty:
        errors.append("Git worktree is dirty")
    if not environment.get("docker_server_version"):
        errors.append("Docker server is unavailable")
    if not environment.get("runtime_image_id"):
        errors.append("runtime image identifier is unavailable")
    if not environment.get("cpu_model"):
        errors.append("CPU model is unavailable")
    if not environment.get("memory_bytes"):
        errors.append("host memory is unavailable")
    if int(environment.get("active_worker_count") or 0) < 1:
        errors.append("no active worker is registered")
    if errors:
        raise SystemExit("Release benchmark preflight failed: " + "; ".join(errors))


def aggregate_scenario(
    scenario: BenchmarkScenario,
    reports: list[BenchmarkReport],
    raw_paths: list[str],
) -> dict[str, Any]:
    median_summary: dict[str, float | None] = {}
    for metric in AGGREGATE_METRICS:
        values = [report.summary.get(metric) for report in reports]
        numeric_values = [float(value) for value in values if isinstance(value, (int, float))]
        median_summary[metric] = (
            round(float(statistics.median(numeric_values)), 2) if numeric_values else None
        )

    return {
        "name": scenario.name,
        "scenario": asdict(scenario),
        "repetitions": len(reports),
        "all_runs_succeeded": all(report.summary.get("success_rate") == 1.0 for report in reports),
        "median_summary": median_summary,
        "runs": [
            {
                "generated_at": report.generated_at,
                "wall_duration_seconds": report.wall_duration_seconds,
                "summary": report.summary,
                "raw_json_path": raw_path,
            }
            for report, raw_path in zip(reports, raw_paths, strict=True)
        ],
    }


def render_release_report(report: dict[str, Any]) -> str:
    environment = report["environment"]
    rows = []
    for scenario in report["scenarios"]:
        config = scenario["scenario"]
        median = scenario["median_summary"]
        rows.append(
            "| {name} | {repetitions} | {invocations} | {concurrency} | {success_rate} | "
            "{throughput} | {p50} | {p95} | {p99} | {queue} | {execution} |".format(
                name=scenario["name"],
                repetitions=scenario["repetitions"],
                invocations=config["invocations"],
                concurrency=config["concurrency"],
                success_rate=median["success_rate"],
                throughput=median["throughput_invocations_per_second"],
                p50=median["p50_latency_ms"],
                p95=median["p95_latency_ms"],
                p99=median["p99_latency_ms"],
                queue=median["average_queue_latency_ms"],
                execution=median["average_execution_latency_ms"],
            )
        )

    evidence_lines = []
    for scenario in report["scenarios"]:
        evidence_lines.append(f"### {scenario['name']}")
        evidence_lines.append("")
        evidence_lines.extend(f"- `{run['raw_json_path']}`" for run in scenario["runs"])
        evidence_lines.append("")

    return "\n".join(
        [
            "# Release Benchmark Report",
            "",
            "Generated by `python3 -m benchmarks.run_release_suite`.",
            "All reported values are medians across independent runs.",
            "",
            "## Reproducibility Metadata",
            "",
            f"- Generated at: {report['generated_at']}",
            f"- Host: {environment.get('host')}",
            f"- Commit SHA: {environment.get('git_commit_sha')}",
            f"- Git worktree clean at start: {environment.get('git_worktree_clean')}",
            f"- Operating system: {environment.get('operating_system')}",
            f"- Architecture: {environment.get('architecture')}",
            f"- CPU: {environment.get('cpu_model')}",
            f"- Logical CPUs: {environment.get('logical_cpu_count')}",
            f"- Memory bytes: {environment.get('memory_bytes')}",
            f"- Docker server: {environment.get('docker_server_version')}",
            f"- Runtime image: {environment.get('runtime_image_id')}",
            f"- Active workers: {environment.get('active_worker_count')}",
            f"- Total worker concurrency: {environment.get('total_worker_concurrency')}",
            f"- Execution mode: {environment.get('execution_mode')}",
            "",
            "## Median Results",
            "",
            (
                "| Scenario | Runs | Invocations | Clients | Success rate | Throughput/s | "
                "p50 ms | p95 ms | p99 ms | Avg queue ms | Avg execution ms |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            *rows,
            "",
            "## Raw Evidence",
            "",
            *evidence_lines,
        ]
    )


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    suite_id = utc_now_iso().replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
    selected = [scenario for scenario in RELEASE_SCENARIOS if scenario.name in args.scenarios]
    environment = collect_release_environment(
        api_url=args.api_url,
        auth_password=args.auth_password,
        http_timeout_seconds=args.http_timeout_seconds,
        suite_id=suite_id,
    )
    validate_release_environment(environment, allow_dirty=args.allow_dirty)

    results_dir = args.results_dir or ROOT_DIR / "benchmarks" / "results" / "release" / suite_id
    scenario_aggregates = []
    for scenario in selected:
        config = scenario_config(
            scenario,
            api_url=args.api_url,
            poll_interval_seconds=args.poll_interval_seconds,
            poll_timeout_seconds=args.poll_timeout_seconds,
            http_timeout_seconds=args.http_timeout_seconds,
        )
        reports = []
        raw_paths = []
        for repetition in range(1, args.repetitions + 1):
            auth_email = (
                f"benchmark-release-{suite_id}-{scenario.name}-{repetition}-"
                f"{uuid.uuid4().hex[:6]}@example.local"
            )
            benchmark_report = run_benchmark(
                config,
                auth_email=auth_email,
                auth_password=args.auth_password,
                environment=environment,
            )
            raw_path = results_dir / f"{scenario.name}-run-{repetition}.json"
            write_json(raw_path, report_to_dict(benchmark_report))
            reports.append(benchmark_report)
            raw_paths.append(relative_to_root(raw_path))
        scenario_aggregates.append(aggregate_scenario(scenario, reports, raw_paths))

    aggregate_report = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "environment": environment,
        "repetitions": args.repetitions,
        "scenarios": scenario_aggregates,
    }
    aggregate_path = results_dir / "aggregate.json"
    write_json(aggregate_path, aggregate_report)
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(render_release_report(aggregate_report), encoding="utf-8")

    print(render_release_report(aggregate_report))
    print(f"Wrote aggregate JSON to {aggregate_path}")
    print(f"Wrote release report to {args.report_path}")
    if not all(scenario["all_runs_succeeded"] for scenario in scenario_aggregates):
        raise SystemExit("At least one release benchmark run did not reach 100% success")


if __name__ == "__main__":
    main()
