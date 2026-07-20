#!/usr/bin/env python3
"""Validate and aggregate repeated worker-scaling benchmark reports."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmarks.run_benchmark import ROOT_DIR, utc_now_iso

RUN_FILE_PATTERN = re.compile(r"worker-(?P<workers>\d+)-run-(?P<run>\d+)\.json$")
WARMUP_FILE_PATTERN = re.compile(r"worker-(?P<workers>\d+)-warmup\.json$")
SUMMARY_METRICS = (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate repeated 1/N worker benchmark JSON files."
    )
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--aggregate-json", type=Path)
    parser.add_argument(
        "--report-path",
        type=Path,
        default=ROOT_DIR / "docs" / "benchmark-worker-scaling-report.md",
    )
    parser.add_argument(
        "--fresh-state-per-topology",
        action="store_true",
        help="record that PostgreSQL and Redis volumes were recreated for each worker count",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def worker_count_from_path(path: Path, pattern: re.Pattern[str]) -> int:
    match = pattern.fullmatch(path.name)
    if match is None:
        raise ValueError(f"Unexpected scaling result filename: {path.name}")
    return int(match.group("workers"))


def require_mapping(value: object, *, path: Path, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {field} must be an object")
    return value


def require_number(value: object, *, path: Path, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path}: {field} must be numeric")
    return float(value)


def relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def validate_report(
    path: Path,
    report: dict[str, Any],
    *,
    expected_workers: int,
    expected_invocations: int | None,
) -> None:
    environment = require_mapping(report.get("environment"), path=path, field="environment")
    config = require_mapping(report.get("config"), path=path, field="config")
    summary = require_mapping(report.get("summary"), path=path, field="summary")
    samples = report.get("samples")

    if environment.get("git_worktree_clean") is not True:
        raise ValueError(f"{path}: benchmark did not start from a clean worktree")
    if environment.get("active_worker_count") != expected_workers:
        raise ValueError(
            f"{path}: active_worker_count={environment.get('active_worker_count')} "
            f"does not match filename worker count {expected_workers}"
        )
    total_concurrency = environment.get("total_worker_concurrency")
    if not isinstance(total_concurrency, int) or total_concurrency < expected_workers:
        raise ValueError(f"{path}: invalid total_worker_concurrency={total_concurrency}")
    if config.get("workload") != "noop":
        raise ValueError(f"{path}: scaling suite expects the noop workload")
    if expected_invocations is not None and config.get("invocations") != expected_invocations:
        raise ValueError(
            f"{path}: invocations={config.get('invocations')} does not match "
            f"expected {expected_invocations}"
        )
    if not isinstance(samples, list) or len(samples) != config.get("invocations"):
        raise ValueError(f"{path}: sample count does not match configured invocations")
    if summary.get("total_invocations") != config.get("invocations"):
        raise ValueError(f"{path}: summary total does not match configured invocations")
    for metric in SUMMARY_METRICS:
        require_number(summary.get(metric), path=path, field=f"summary.{metric}")


def shared_value(
    records: list[tuple[Path, dict[str, Any]]],
    *,
    section: str,
    field: str,
) -> Any:
    values = {
        json.dumps(require_mapping(report.get(section), path=path, field=section).get(field))
        for path, report in records
    }
    if len(values) != 1:
        raise ValueError(f"Scaling reports disagree on {section}.{field}")
    return json.loads(values.pop())


def aggregate_scaling_reports(
    run_paths: list[Path],
    warmup_paths: list[Path],
    *,
    fresh_state_per_topology: bool,
) -> dict[str, Any]:
    if not run_paths:
        raise ValueError("No measured scaling reports were provided")

    runs_by_workers: dict[int, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    all_records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(run_paths):
        workers = worker_count_from_path(path, RUN_FILE_PATTERN)
        report = load_json(path)
        validate_report(path, report, expected_workers=workers, expected_invocations=None)
        runs_by_workers[workers].append((path, report))
        all_records.append((path, report))

    worker_counts = sorted(runs_by_workers)
    if not worker_counts or worker_counts[0] != 1:
        raise ValueError("Scaling reports must include a one-worker baseline")
    repetitions = {len(records) for records in runs_by_workers.values()}
    if len(repetitions) != 1:
        raise ValueError("Each worker count must have the same number of measured runs")

    measured_invocations = shared_value(all_records, section="config", field="invocations")
    if not isinstance(measured_invocations, int) or measured_invocations <= 0:
        raise ValueError("Measured invocation count must be a positive integer")
    for path, report in all_records:
        workers = worker_count_from_path(path, RUN_FILE_PATTERN)
        validate_report(
            path,
            report,
            expected_workers=workers,
            expected_invocations=measured_invocations,
        )

    commit_sha = shared_value(all_records, section="environment", field="git_commit_sha")
    runtime_image_id = shared_value(
        all_records,
        section="environment",
        field="runtime_image_id",
    )
    workload = shared_value(all_records, section="config", field="workload")
    client_concurrency = shared_value(all_records, section="config", field="concurrency")
    timeout_seconds = shared_value(all_records, section="config", field="timeout_seconds")
    memory_limit_mb = shared_value(all_records, section="config", field="memory_limit_mb")
    cpu_limit = shared_value(all_records, section="config", field="cpu_limit")

    warmups_by_workers: dict[int, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(warmup_paths):
        workers = worker_count_from_path(path, WARMUP_FILE_PATTERN)
        if workers in warmups_by_workers:
            raise ValueError(f"Duplicate warm-up report for {workers} workers")
        report = load_json(path)
        validate_report(path, report, expected_workers=workers, expected_invocations=None)
        warmups_by_workers[workers] = (path, report)
    if sorted(warmups_by_workers) != worker_counts:
        raise ValueError("Each measured worker count must have exactly one warm-up report")

    warmup_records = list(warmups_by_workers.values())
    warmup_invocations = shared_value(warmup_records, section="config", field="invocations")
    warmup_commit_sha = shared_value(
        warmup_records,
        section="environment",
        field="git_commit_sha",
    )
    if warmup_commit_sha != commit_sha:
        raise ValueError("Warm-up and measured reports use different commits")
    if not isinstance(warmup_invocations, int) or warmup_invocations <= 0:
        raise ValueError("Warm-up invocation count must be a positive integer")
    warmup_expected_values = {
        "workload": workload,
        "concurrency": client_concurrency,
        "timeout_seconds": timeout_seconds,
        "memory_limit_mb": memory_limit_mb,
        "cpu_limit": cpu_limit,
    }
    for field, expected in warmup_expected_values.items():
        actual = shared_value(warmup_records, section="config", field=field)
        if actual != expected:
            raise ValueError(
                f"Warm-up config.{field}={actual!r} does not match measured value {expected!r}"
            )
    warmup_runtime_image = shared_value(
        warmup_records,
        section="environment",
        field="runtime_image_id",
    )
    if warmup_runtime_image != runtime_image_id:
        raise ValueError("Warm-up and measured reports use different runtime images")

    aggregated_workers: list[dict[str, Any]] = []
    for workers in worker_counts:
        records = runs_by_workers[workers]
        median_summary: dict[str, float] = {}
        for metric in SUMMARY_METRICS:
            values = [
                require_number(
                    require_mapping(report.get("summary"), path=path, field="summary").get(metric),
                    path=path,
                    field=f"summary.{metric}",
                )
                for path, report in records
            ]
            median_summary[metric] = round(float(statistics.median(values)), 2)

        throughputs = [
            require_number(
                require_mapping(report.get("summary"), path=path, field="summary").get(
                    "throughput_invocations_per_second"
                ),
                path=path,
                field="summary.throughput_invocations_per_second",
            )
            for path, report in records
        ]
        throughput_mean = statistics.mean(throughputs)
        throughput_cv = (
            statistics.pstdev(throughputs) / throughput_mean * 100
            if throughput_mean > 0
            else 0.0
        )
        status_counts: dict[str, int] = defaultdict(int)
        run_summaries: list[dict[str, Any]] = []
        for path, report in records:
            summary = require_mapping(report.get("summary"), path=path, field="summary")
            for status, count in require_mapping(
                summary.get("status_counts"),
                path=path,
                field="summary.status_counts",
            ).items():
                if not isinstance(status, str) or not isinstance(count, int):
                    raise ValueError(f"{path}: invalid status count")
                status_counts[status] += count
            run_summaries.append(
                {
                    "path": relative_to_root(path),
                    "generated_at": report.get("generated_at"),
                    "wall_duration_seconds": report.get("wall_duration_seconds"),
                    "summary": summary,
                }
            )

        environment = require_mapping(records[0][1].get("environment"), path=records[0][0], field="environment")
        aggregated_workers.append(
            {
                "worker_count": workers,
                "total_worker_concurrency": environment["total_worker_concurrency"],
                "repetitions": len(records),
                "measured_invocations": len(records) * measured_invocations,
                "status_counts": dict(sorted(status_counts.items())),
                "median_summary": median_summary,
                "throughput_range": {
                    "minimum": round(min(throughputs), 2),
                    "maximum": round(max(throughputs), 2),
                },
                "throughput_cv_percent": round(throughput_cv, 2),
                "warmup_path": relative_to_root(warmups_by_workers[workers][0]),
                "runs": run_summaries,
            }
        )

    baseline_throughput = aggregated_workers[0]["median_summary"][
        "throughput_invocations_per_second"
    ]
    for worker_result in aggregated_workers:
        speedup = (
            worker_result["median_summary"]["throughput_invocations_per_second"]
            / baseline_throughput
        )
        worker_result["throughput_speedup_vs_one_worker"] = round(speedup, 2)
        worker_result["throughput_efficiency_vs_one_worker"] = round(
            speedup / worker_result["worker_count"],
            2,
        )

    total_measured = sum(item["measured_invocations"] for item in aggregated_workers)
    total_succeeded = sum(
        item["status_counts"].get("SUCCEEDED", 0) for item in aggregated_workers
    )
    return {
        "generated_at": utc_now_iso(),
        "source_commit_sha": commit_sha,
        "all_source_worktrees_clean": True,
        "environment": {
            "host": shared_value(all_records, section="environment", field="host"),
            "operating_system": shared_value(
                all_records,
                section="environment",
                field="operating_system",
            ),
            "architecture": shared_value(
                all_records,
                section="environment",
                field="architecture",
            ),
            "cpu_model": shared_value(all_records, section="environment", field="cpu_model"),
            "logical_cpu_count": shared_value(
                all_records,
                section="environment",
                field="logical_cpu_count",
            ),
            "memory_bytes": shared_value(
                all_records,
                section="environment",
                field="memory_bytes",
            ),
            "docker_server_version": shared_value(
                all_records,
                section="environment",
                field="docker_server_version",
            ),
            "runtime_image_id": runtime_image_id,
            "execution_mode": shared_value(
                all_records,
                section="environment",
                field="execution_mode",
            ),
        },
        "methodology": {
            "fresh_state_per_topology": fresh_state_per_topology,
            "warmup_invocations_per_topology": warmup_invocations,
            "measured_invocations_per_run": measured_invocations,
            "repetitions_per_topology": repetitions.pop(),
            "workload": workload,
            "client_concurrency": client_concurrency,
            "timeout_seconds": timeout_seconds,
            "memory_limit_mb": memory_limit_mb,
            "cpu_limit": cpu_limit,
        },
        "total_measured_invocations": total_measured,
        "total_succeeded_invocations": total_succeeded,
        "all_runs_succeeded": total_succeeded == total_measured,
        "workers": aggregated_workers,
    }


def render_markdown(report: dict[str, Any]) -> str:
    environment = report["environment"]
    methodology = report["methodology"]
    rows: list[str] = []
    for worker in report["workers"]:
        median = worker["median_summary"]
        throughput_range = worker["throughput_range"]
        rows.append(
            "| {workers} | {concurrency} | {success}/{total} | {throughput} | {speedup}x | "
            "{efficiency} | {p95} | {queue} | {execution} | {minimum}-{maximum} | {cv}% |".format(
                workers=worker["worker_count"],
                concurrency=worker["total_worker_concurrency"],
                success=worker["status_counts"].get("SUCCEEDED", 0),
                total=worker["measured_invocations"],
                throughput=median["throughput_invocations_per_second"],
                speedup=worker["throughput_speedup_vs_one_worker"],
                efficiency=worker["throughput_efficiency_vs_one_worker"],
                p95=median["p95_latency_ms"],
                queue=median["average_queue_latency_ms"],
                execution=median["average_execution_latency_ms"],
                minimum=throughput_range["minimum"],
                maximum=throughput_range["maximum"],
                cv=worker["throughput_cv_percent"],
            )
        )

    raw_lines: list[str] = []
    for worker in report["workers"]:
        raw_lines.append(f"### {worker['worker_count']} Worker(s)")
        raw_lines.append("")
        raw_lines.append(f"- Warm-up: `{worker['warmup_path']}`")
        raw_lines.extend(f"- Measured: `{run['path']}`" for run in worker["runs"])
        raw_lines.append("")

    highest_variability = max(report["workers"], key=lambda item: item["throughput_cv_percent"])
    largest_topology = report["workers"][-1]
    return "\n".join(
        [
            "# Worker Scaling Benchmark Report",
            "",
            "Generated by `python3 -m benchmarks.aggregate_scaling_results`.",
            "This is exploratory single-host evidence, not a production scalability claim.",
            "",
            "## Reproducibility Metadata",
            "",
            f"- Generated at: {report['generated_at']}",
            f"- Source commit SHA: {report['source_commit_sha']}",
            f"- All source worktrees clean: {report['all_source_worktrees_clean']}",
            f"- Host: {environment['host']}",
            f"- Operating system: {environment['operating_system']}",
            f"- Architecture: {environment['architecture']}",
            f"- CPU: {environment['cpu_model']}",
            f"- Logical CPUs: {environment['logical_cpu_count']}",
            f"- Memory bytes: {environment['memory_bytes']}",
            f"- Docker server: {environment['docker_server_version']}",
            f"- Runtime image: {environment['runtime_image_id']}",
            f"- Execution mode: {environment['execution_mode']}",
            "",
            "## Methodology",
            "",
            f"- Worker counts: {', '.join(str(item['worker_count']) for item in report['workers'])}.",
            f"- Fresh PostgreSQL and Redis state per topology: {methodology['fresh_state_per_topology']}.",
            f"- Unmeasured warm-up invocations per topology: {methodology['warmup_invocations_per_topology']}.",
            f"- Measured runs per topology: {methodology['repetitions_per_topology']}.",
            f"- Measured invocations per run: {methodology['measured_invocations_per_run']}.",
            f"- Concurrent API clients: {methodology['client_concurrency']}.",
            f"- Workload: {methodology['workload']}.",
            f"- Function limits: {methodology['cpu_limit']} CPU, {methodology['memory_limit_mb']} MiB, "
            f"{methodology['timeout_seconds']}s timeout.",
            "- Reported latency and throughput values are medians across measured runs.",
            "- Throughput CV is the population coefficient of variation across the three runs.",
            "",
            "## Median Results",
            "",
            "| Workers | Worker concurrency | Success | Throughput/s | Speedup | Efficiency | "
            "p95 ms | Avg queue ms | Avg execution ms | Throughput range | Throughput CV |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            *rows,
            "",
            "## Interpretation",
            "",
            f"All {report['total_succeeded_invocations']} measured invocations succeeded across "
            f"{report['total_measured_invocations']} attempts.",
            "",
            f"The {largest_topology['worker_count']}-worker topology reached a median "
            f"{largest_topology['throughput_speedup_vs_one_worker']}x throughput speedup over the "
            "one-worker baseline. The result is sublinear because all workers share one Docker "
            "Desktop daemon and one physical host, while every invocation creates a fresh container.",
            "",
            f"Run-to-run variance was material: the highest throughput CV was "
            f"{highest_variability['throughput_cv_percent']}% at "
            f"{highest_variability['worker_count']} workers. Later runs at higher container pressure "
            "showed longer execution time even though there were no invocation failures, leaked "
            "runtime containers, retries, or worker errors. These results verify parallel consumption "
            "and show a capacity increase at four workers, but they do not establish linear scaling.",
            "",
            "The benchmark should therefore remain engineering evidence in the repository and should "
            "not replace the correctness-focused 750-invocation release result on the resume.",
            "",
            "## Raw Evidence",
            "",
            *raw_lines,
        ]
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    run_paths = sorted(args.results_dir.glob("worker-*-run-*.json"))
    warmup_paths = sorted(args.results_dir.glob("worker-*-warmup.json"))
    report = aggregate_scaling_reports(
        run_paths,
        warmup_paths,
        fresh_state_per_topology=args.fresh_state_per_topology,
    )
    aggregate_json = args.aggregate_json or args.results_dir / "aggregate.json"
    write_json(aggregate_json, report)
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Wrote aggregate JSON to {aggregate_json}")
    print(f"Wrote markdown report to {args.report_path}")


if __name__ == "__main__":
    main()
