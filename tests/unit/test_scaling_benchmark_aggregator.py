import json
from pathlib import Path

import pytest

from benchmarks.aggregate_scaling_results import (
    aggregate_scaling_reports,
    render_markdown,
)


def write_report(
    path: Path,
    *,
    workers: int,
    invocations: int,
    throughput: float,
    p95: float,
) -> None:
    report = {
        "generated_at": "2026-07-20T00:00:00Z",
        "wall_duration_seconds": round(invocations / throughput, 3),
        "environment": {
            "host": "benchmark-host",
            "git_commit_sha": "abc123",
            "git_worktree_clean": True,
            "operating_system": "Linux",
            "architecture": "x86_64",
            "cpu_model": "Test CPU",
            "logical_cpu_count": 8,
            "memory_bytes": 16_000_000_000,
            "docker_server_version": "29.0.0",
            "runtime_image_id": "sha256:test",
            "active_worker_count": workers,
            "total_worker_concurrency": workers * 2,
            "execution_mode": "cold container per invocation",
        },
        "config": {
            "workload": "noop",
            "invocations": invocations,
            "concurrency": 10,
            "timeout_seconds": 30,
            "memory_limit_mb": 256,
            "cpu_limit": 0.5,
        },
        "summary": {
            "total_invocations": invocations,
            "status_counts": {"SUCCEEDED": invocations},
            "throughput_invocations_per_second": throughput,
            "success_rate": 1.0,
            "error_rate": 0.0,
            "timeout_rate": 0.0,
            "p50_latency_ms": p95 / 2,
            "p95_latency_ms": p95,
            "p99_latency_ms": p95 * 1.1,
            "average_queue_latency_ms": p95 / 3,
            "average_execution_latency_ms": p95 / 4,
            "average_accept_latency_ms": 10.0,
        },
        "samples": [{} for _ in range(invocations)],
    }
    path.write_text(json.dumps(report), encoding="utf-8")


def test_aggregate_scaling_reports_calculates_medians_and_speedup(tmp_path: Path) -> None:
    run_paths: list[Path] = []
    warmup_paths: list[Path] = []
    for workers, throughputs in ((1, [2.0, 3.0, 4.0]), (2, [4.0, 6.0, 8.0])):
        warmup = tmp_path / f"worker-{workers}-warmup.json"
        write_report(
            warmup,
            workers=workers,
            invocations=20,
            throughput=throughputs[0],
            p95=1000.0,
        )
        warmup_paths.append(warmup)
        for run_number, throughput in enumerate(throughputs, start=1):
            path = tmp_path / f"worker-{workers}-run-{run_number}.json"
            write_report(
                path,
                workers=workers,
                invocations=100,
                throughput=throughput,
                p95=1000.0 / workers,
            )
            run_paths.append(path)

    aggregate = aggregate_scaling_reports(
        run_paths,
        warmup_paths,
        fresh_state_per_topology=True,
    )

    assert aggregate["total_measured_invocations"] == 600
    assert aggregate["total_succeeded_invocations"] == 600
    assert aggregate["all_runs_succeeded"] is True
    assert aggregate["workers"][0]["median_summary"][
        "throughput_invocations_per_second"
    ] == 3.0
    assert aggregate["workers"][1]["median_summary"][
        "throughput_invocations_per_second"
    ] == 6.0
    assert aggregate["workers"][1]["throughput_speedup_vs_one_worker"] == 2.0
    assert aggregate["workers"][1]["throughput_efficiency_vs_one_worker"] == 1.0

    markdown = render_markdown(aggregate)
    assert "exploratory single-host evidence" in markdown
    assert "600 measured invocations succeeded" in markdown


def test_aggregate_scaling_reports_rejects_topology_mismatch(tmp_path: Path) -> None:
    run_path = tmp_path / "worker-1-run-1.json"
    warmup_path = tmp_path / "worker-1-warmup.json"
    write_report(run_path, workers=2, invocations=100, throughput=4.0, p95=1000.0)
    write_report(warmup_path, workers=1, invocations=20, throughput=2.0, p95=1000.0)

    with pytest.raises(ValueError, match="does not match filename worker count"):
        aggregate_scaling_reports(
            [run_path],
            [warmup_path],
            fresh_state_per_topology=True,
        )


def test_aggregate_scaling_reports_rejects_warmup_config_mismatch(tmp_path: Path) -> None:
    run_path = tmp_path / "worker-1-run-1.json"
    warmup_path = tmp_path / "worker-1-warmup.json"
    write_report(run_path, workers=1, invocations=100, throughput=3.0, p95=1000.0)
    write_report(warmup_path, workers=1, invocations=20, throughput=2.0, p95=1000.0)
    warmup = json.loads(warmup_path.read_text(encoding="utf-8"))
    warmup["config"]["concurrency"] = 5
    warmup_path.write_text(json.dumps(warmup), encoding="utf-8")

    with pytest.raises(ValueError, match="Warm-up config.concurrency"):
        aggregate_scaling_reports(
            [run_path],
            [warmup_path],
            fresh_state_per_topology=True,
        )
