import pytest

from benchmarks.run_benchmark import BenchmarkConfig, BenchmarkReport, InvocationSample
from benchmarks.run_release_suite import (
    RELEASE_SCENARIOS,
    aggregate_scenario,
    render_release_report,
    scenario_config,
    validate_release_environment,
)


def make_report(*, throughput: float, p95: float, success_rate: float = 1.0) -> BenchmarkReport:
    config = BenchmarkConfig(
        api_url="http://localhost:8000",
        workload="noop",
        function_name="bench-release-noop",
        invocations=100,
        concurrency=10,
        payload={},
        timeout_seconds=30,
        memory_limit_mb=256,
        cpu_limit=0.5,
        poll_interval_seconds=0.25,
        poll_timeout_seconds=180.0,
        http_timeout_seconds=15.0,
    )
    summary = {
        "total_invocations": 100,
        "status_counts": {"SUCCEEDED": int(100 * success_rate)},
        "throughput_invocations_per_second": throughput,
        "success_rate": success_rate,
        "error_rate": round(1 - success_rate, 2),
        "timeout_rate": 0.0,
        "p50_latency_ms": p95 - 100,
        "p95_latency_ms": p95,
        "p99_latency_ms": p95 + 100,
        "average_queue_latency_ms": p95 - 200,
        "average_execution_latency_ms": 200.0,
        "average_accept_latency_ms": 10.0,
    }
    return BenchmarkReport(
        generated_at="2026-07-14T00:00:00Z",
        host="test-host",
        wall_duration_seconds=10.0,
        config=config,
        summary=summary,
        samples=[InvocationSample(index=0, idempotency_key="key", status="SUCCEEDED")],
        environment={},
    )


def test_aggregate_scenario_uses_median_across_independent_runs() -> None:
    reports = [
        make_report(throughput=4.0, p95=1200),
        make_report(throughput=6.0, p95=1000),
        make_report(throughput=5.0, p95=1100),
    ]

    aggregate = aggregate_scenario(
        RELEASE_SCENARIOS[0],
        reports,
        ["run-1.json", "run-2.json", "run-3.json"],
    )

    assert aggregate["repetitions"] == 3
    assert aggregate["all_runs_succeeded"] is True
    assert aggregate["median_summary"]["throughput_invocations_per_second"] == 5.0
    assert aggregate["median_summary"]["p95_latency_ms"] == 1100.0
    assert [run["raw_json_path"] for run in aggregate["runs"]] == [
        "run-1.json",
        "run-2.json",
        "run-3.json",
    ]


def test_release_preflight_rejects_dirty_or_incomplete_environment() -> None:
    environment = {
        "git_commit_sha": "abc123",
        "git_worktree_clean": False,
        "docker_server_version": "29.0.0",
        "runtime_image_id": "sha256:runtime",
        "cpu_model": "test-cpu",
        "memory_bytes": 16_000_000_000,
        "active_worker_count": 1,
    }

    with pytest.raises(SystemExit, match="Git worktree is dirty"):
        validate_release_environment(environment, allow_dirty=False)

    validate_release_environment(environment, allow_dirty=True)


def test_render_release_report_includes_reproducibility_and_raw_evidence() -> None:
    scenario = aggregate_scenario(
        RELEASE_SCENARIOS[0],
        [make_report(throughput=5.0, p95=1100)],
        ["benchmarks/results/release/run-1.json"],
    )
    report = {
        "generated_at": "2026-07-14T00:00:00Z",
        "environment": {
            "host": "test-host",
            "git_commit_sha": "abc123",
            "git_worktree_clean": True,
            "operating_system": "test-os",
            "architecture": "arm64",
            "cpu_model": "test-cpu",
            "logical_cpu_count": 8,
            "memory_bytes": 16_000_000_000,
            "docker_server_version": "29.0.0",
            "runtime_image_id": "sha256:runtime",
            "active_worker_count": 1,
            "total_worker_concurrency": 2,
            "execution_mode": "cold container per invocation",
        },
        "scenarios": [scenario],
    }

    markdown = render_release_report(report)

    assert "# Release Benchmark Report" in markdown
    assert "- Host: test-host" in markdown
    assert "- Commit SHA: abc123" in markdown
    assert "All reported values are medians" in markdown
    assert "| noop-100x10 | 1 | 100 | 10 | 1.0 | 5.0 |" in markdown
    assert "`benchmarks/results/release/run-1.json`" in markdown


def test_scenario_config_preserves_release_workload_dimensions() -> None:
    config = scenario_config(
        RELEASE_SCENARIOS[2],
        api_url="http://localhost:8000",
        poll_interval_seconds=0.25,
        poll_timeout_seconds=180.0,
        http_timeout_seconds=15.0,
    )

    assert config.workload == "cpu_bound"
    assert config.invocations == 50
    assert config.concurrency == 5
    assert config.payload == {"n": 250000}
    assert config.function_name == "bench-release-cpu_bound"
