import io
import zipfile

from benchmarks.run_benchmark import (
    ApiClient,
    BenchmarkConfig,
    BenchmarkReport,
    InvocationSample,
    encode_multipart,
    package_workload,
    render_markdown_report,
    summarize_samples,
)


class RecordingApiClient(ApiClient):
    def __init__(self) -> None:
        super().__init__("http://testserver", timeout_seconds=1)
        self.calls: list[tuple[str, str, object, tuple[int, ...]]] = []

    def json_request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, object]:
        self.calls.append((method, path, payload, expected_statuses))
        if path == "/auth/login":
            return 200, {"access_token": "signed-token"}
        return 201, {"id": "user-id"}


def test_api_client_registers_and_logs_in_without_storing_password_in_config() -> None:
    client = RecordingApiClient()

    client.authenticate("benchmark@example.local", "benchmark-password")

    assert client.access_token == "signed-token"
    assert client.calls == [
        (
            "POST",
            "/auth/register",
            {"email": "benchmark@example.local", "password": "benchmark-password"},
            (201, 409),
        ),
        (
            "POST",
            "/auth/login",
            {"email": "benchmark@example.local", "password": "benchmark-password"},
            (200,),
        ),
    ]


def test_summarize_samples_calculates_rates_and_latency_percentiles() -> None:
    samples = [
        InvocationSample(
            index=0,
            idempotency_key="bench-0",
            status="SUCCEEDED",
            end_to_end_latency_ms=100,
            queue_latency_ms=10,
            execution_latency_ms=70,
            accepted_latency_ms=5,
        ),
        InvocationSample(
            index=1,
            idempotency_key="bench-1",
            status="FAILED",
            end_to_end_latency_ms=200,
            queue_latency_ms=20,
            execution_latency_ms=120,
            accepted_latency_ms=7,
        ),
        InvocationSample(
            index=2,
            idempotency_key="bench-2",
            status="TIMEOUT",
            end_to_end_latency_ms=400,
            queue_latency_ms=30,
            execution_latency_ms=300,
            accepted_latency_ms=9,
        ),
        InvocationSample(
            index=3,
            idempotency_key="bench-3",
            status="CLIENT_ERROR",
            client_error="connection refused",
        ),
    ]

    summary = summarize_samples(samples, wall_duration_seconds=2.0)

    assert summary == {
        "total_invocations": 4,
        "status_counts": {
            "CLIENT_ERROR": 1,
            "FAILED": 1,
            "SUCCEEDED": 1,
            "TIMEOUT": 1,
        },
        "throughput_invocations_per_second": 2.0,
        "success_rate": 0.25,
        "error_rate": 0.75,
        "timeout_rate": 0.25,
        "p50_latency_ms": 200,
        "p95_latency_ms": 400,
        "p99_latency_ms": 400,
        "average_queue_latency_ms": 20.0,
        "average_execution_latency_ms": 163.33,
        "average_accept_latency_ms": 7.0,
    }


def test_package_workload_creates_zip_with_handler_module() -> None:
    package_bytes = package_workload("noop")

    with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
        assert archive.namelist() == ["main.py"]
        assert "def handler(event, context):" in archive.read("main.py").decode("utf-8")


def test_encode_multipart_includes_form_fields_and_file_content() -> None:
    body, content_type = encode_multipart(
        fields={"runtime": "python3.11", "timeout_seconds": "30"},
        files={"package": ("function.zip", b"zip-bytes", "application/zip")},
    )

    assert content_type.startswith("multipart/form-data; boundary=benchmark-")
    assert b'name="runtime"' in body
    assert b"python3.11" in body
    assert b'filename="function.zip"' in body
    assert b"zip-bytes" in body


def test_render_markdown_report_includes_metrics_and_failure_injection_command() -> None:
    config = BenchmarkConfig(
        api_url="http://localhost:8000",
        workload="noop",
        function_name="bench-noop",
        invocations=1,
        concurrency=1,
        payload={},
        timeout_seconds=30,
        memory_limit_mb=256,
        cpu_limit=0.5,
        poll_interval_seconds=0.25,
        poll_timeout_seconds=120,
        http_timeout_seconds=15,
    )
    samples = [
        InvocationSample(
            index=0,
            idempotency_key="bench-0",
            status="SUCCEEDED",
            end_to_end_latency_ms=42,
        )
    ]
    report = BenchmarkReport(
        generated_at="2026-07-06T00:00:00Z",
        host="test-host",
        wall_duration_seconds=1.0,
        config=config,
        summary=summarize_samples(samples, wall_duration_seconds=1.0),
        samples=samples,
    )

    markdown = render_markdown_report(report)

    assert "# Benchmark Report" in markdown
    assert "- Workload: noop" in markdown
    assert "| throughput_invocations_per_second | 1.0 |" in markdown
    assert "tests/failure_injection/test_worker_crash_recovery.py" in markdown
