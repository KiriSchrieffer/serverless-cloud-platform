#!/usr/bin/env python3
"""Run local invocation benchmarks against the Serverless Cloud Platform API."""

from __future__ import annotations

import argparse
import io
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
WORKLOADS_DIR = ROOT_DIR / "benchmarks" / "workloads"
DEFAULT_REPORT_PATH = ROOT_DIR / "docs" / "benchmark-report.md"
DEFAULT_JSON_OUTPUT_PATH = ROOT_DIR / "benchmarks" / "results" / "latest.json"

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "TIMEOUT", "CANCELED"}
NON_SUCCESS_STATUSES = {"FAILED", "TIMEOUT", "CANCELED", "CLIENT_ERROR", "POLL_TIMEOUT"}


class ApiError(Exception):
    def __init__(self, method: str, path: str, status_code: int, response_body: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status_code}: {response_body}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.response_body = response_body


@dataclass(frozen=True)
class BenchmarkConfig:
    api_url: str
    workload: str
    function_name: str
    invocations: int
    concurrency: int
    payload: Any
    timeout_seconds: int
    memory_limit_mb: int
    cpu_limit: float
    poll_interval_seconds: float
    poll_timeout_seconds: float
    http_timeout_seconds: float


@dataclass(frozen=True)
class InvocationSample:
    index: int
    idempotency_key: str
    status: str
    invocation_id: str | None = None
    accepted_latency_ms: float | None = None
    end_to_end_latency_ms: float | None = None
    queue_latency_ms: float | None = None
    execution_latency_ms: float | None = None
    error_type: str | None = None
    error_message: str | None = None
    client_error: str | None = None


@dataclass(frozen=True)
class BenchmarkReport:
    generated_at: str
    host: str
    wall_duration_seconds: float
    config: BenchmarkConfig
    summary: dict[str, Any]
    samples: list[InvocationSample]


class ApiClient:
    def __init__(self, api_url: str, *, timeout_seconds: float) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def json_request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        *,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self.request(
            method,
            path,
            data=data,
            headers=headers,
            expected_statuses=expected_statuses,
        )

    def multipart_request(
        self,
        method: str,
        path: str,
        *,
        fields: Mapping[str, str],
        files: Mapping[str, tuple[str, bytes, str]],
        expected_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        body, content_type = encode_multipart(fields=fields, files=files)
        return self.request(
            method,
            path,
            data=body,
            headers={"Accept": "application/json", "Content-Type": content_type},
            expected_statuses=expected_statuses,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None,
        headers: Mapping[str, str],
        expected_statuses: tuple[int, ...],
    ) -> tuple[int, Any]:
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=data,
            headers=dict(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                status_code = response.status
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            response_body = exc.read().decode("utf-8", errors="replace")

        if status_code not in expected_statuses:
            raise ApiError(method, path, status_code, response_body)

        if not response_body:
            return status_code, None
        return status_code, json.loads(response_body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the local API -> Redis Streams -> worker invocation path."
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--workload", choices=workload_names(), default="noop")
    parser.add_argument("--function-name")
    parser.add_argument("--invocations", type=positive_int, default=20)
    parser.add_argument("--concurrency", type=positive_int, default=5)
    parser.add_argument("--payload", default="{}")
    parser.add_argument("--payload-file", type=Path)
    parser.add_argument("--timeout-seconds", type=positive_int, default=30)
    parser.add_argument("--memory-limit-mb", type=positive_int, default=256)
    parser.add_argument("--cpu-limit", type=float, default=0.5)
    parser.add_argument("--poll-interval-seconds", type=positive_float, default=0.25)
    parser.add_argument("--poll-timeout-seconds", type=positive_float, default=120.0)
    parser.add_argument("--http-timeout-seconds", type=positive_float, default=15.0)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--json-output-path", type=Path, default=DEFAULT_JSON_OUTPUT_PATH)
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def workload_names() -> list[str]:
    return sorted(path.name for path in WORKLOADS_DIR.iterdir() if (path / "main.py").is_file())


def build_config(args: argparse.Namespace) -> BenchmarkConfig:
    function_name = args.function_name or f"bench-{args.workload}"
    return BenchmarkConfig(
        api_url=args.api_url,
        workload=args.workload,
        function_name=function_name,
        invocations=args.invocations,
        concurrency=args.concurrency,
        payload=load_payload(args.payload, args.payload_file),
        timeout_seconds=args.timeout_seconds,
        memory_limit_mb=args.memory_limit_mb,
        cpu_limit=args.cpu_limit,
        poll_interval_seconds=args.poll_interval_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
        http_timeout_seconds=args.http_timeout_seconds,
    )


def load_payload(payload_json: str, payload_file: Path | None) -> Any:
    raw_payload = payload_file.read_text(encoding="utf-8") if payload_file else payload_json
    try:
        return json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON payload: {exc}") from exc


def package_workload(workload: str) -> bytes:
    workload_dir = WORKLOADS_DIR / workload
    if not (workload_dir / "main.py").is_file():
        raise SystemExit(f"Workload '{workload}' must contain main.py")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(workload_dir.rglob("*")):
            if should_include_package_file(path):
                archive.write(path, path.relative_to(workload_dir).as_posix())
    return buffer.getvalue()


def should_include_package_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if "__pycache__" in path.parts:
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def encode_multipart(
    *,
    fields: Mapping[str, str],
    files: Mapping[str, tuple[str, bytes, str]],
) -> tuple[bytes, str]:
    boundary = f"benchmark-{uuid.uuid4().hex}"
    lines: list[bytes] = []

    for name, value in fields.items():
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )

    for name, (filename, content, content_type) in files.items():
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                (
                    "Content-Disposition: form-data; "
                    f'name="{name}"; filename="{filename}"'
                ).encode("utf-8"),
                f"Content-Type: {content_type}".encode("utf-8"),
                b"",
                content,
            ]
        )

    lines.extend([f"--{boundary}--".encode("utf-8"), b""])
    return b"\r\n".join(lines), f"multipart/form-data; boundary={boundary}"


def prepare_function(api: ApiClient, config: BenchmarkConfig) -> dict[str, Any]:
    quoted_name = quote_path(config.function_name)
    api.json_request(
        "POST",
        "/functions",
        {"name": config.function_name},
        expected_statuses=(201, 409),
    )
    _, version = api.multipart_request(
        "POST",
        f"/functions/{quoted_name}/versions/upload",
        fields={
            "runtime": "python3.11",
            "handler": "main.handler",
            "memory_limit_mb": str(config.memory_limit_mb),
            "cpu_limit": str(config.cpu_limit),
            "timeout_seconds": str(config.timeout_seconds),
        },
        files={"package": ("function.zip", package_workload(config.workload), "application/zip")},
        expected_statuses=(201,),
    )
    return version


def run_benchmark(config: BenchmarkConfig) -> BenchmarkReport:
    api = ApiClient(config.api_url, timeout_seconds=config.http_timeout_seconds)
    prepare_function(api, config)

    idempotency_prefix = f"{config.workload}-{timestamp_for_key()}-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()
    samples: list[InvocationSample] = []
    with ThreadPoolExecutor(max_workers=config.concurrency) as executor:
        futures = [
            executor.submit(run_single_invocation, api, config, index, idempotency_prefix)
            for index in range(config.invocations)
        ]
        for future in as_completed(futures):
            samples.append(future.result())

    wall_duration_seconds = round(time.perf_counter() - started, 3)
    samples.sort(key=lambda sample: sample.index)
    return build_report(config, samples, wall_duration_seconds)


def run_single_invocation(
    api: ApiClient,
    config: BenchmarkConfig,
    index: int,
    idempotency_prefix: str,
) -> InvocationSample:
    quoted_name = quote_path(config.function_name)
    idempotency_key = f"{idempotency_prefix}-{index}"
    started = time.perf_counter()
    try:
        accepted_started = time.perf_counter()
        _, accepted = api.json_request(
            "POST",
            f"/functions/{quoted_name}/invoke",
            {
                "payload": config.payload,
                "idempotency_key": idempotency_key,
            },
            expected_statuses=(202,),
        )
        accepted_latency_ms = elapsed_perf_ms(accepted_started, time.perf_counter())
    except Exception as exc:
        return InvocationSample(
            index=index,
            idempotency_key=idempotency_key,
            status="CLIENT_ERROR",
            client_error=str(exc),
        )

    invocation_id = accepted["invocation_id"]
    deadline = time.perf_counter() + config.poll_timeout_seconds
    last_invocation: dict[str, Any] | None = None
    while time.perf_counter() < deadline:
        try:
            _, last_invocation = api.json_request(
                "GET",
                f"/invocations/{quote_path(invocation_id)}",
                expected_statuses=(200,),
            )
        except Exception as exc:
            return InvocationSample(
                index=index,
                idempotency_key=idempotency_key,
                status="CLIENT_ERROR",
                invocation_id=invocation_id,
                accepted_latency_ms=accepted_latency_ms,
                client_error=str(exc),
            )

        status = last_invocation["status"]
        if status in TERMINAL_STATUSES:
            return sample_from_invocation(
                index=index,
                idempotency_key=idempotency_key,
                invocation=last_invocation,
                accepted_latency_ms=accepted_latency_ms,
                end_to_end_latency_ms=elapsed_perf_ms(started, time.perf_counter()),
            )
        time.sleep(config.poll_interval_seconds)

    return InvocationSample(
        index=index,
        idempotency_key=idempotency_key,
        status="POLL_TIMEOUT",
        invocation_id=invocation_id,
        accepted_latency_ms=accepted_latency_ms,
        end_to_end_latency_ms=elapsed_perf_ms(started, time.perf_counter()),
        error_message=(
            f"Invocation did not reach a terminal state within "
            f"{config.poll_timeout_seconds} seconds"
        ),
        client_error=json.dumps(last_invocation, sort_keys=True) if last_invocation else None,
    )


def sample_from_invocation(
    *,
    index: int,
    idempotency_key: str,
    invocation: Mapping[str, Any],
    accepted_latency_ms: float,
    end_to_end_latency_ms: float,
) -> InvocationSample:
    queued_at = parse_datetime(invocation.get("queued_at"))
    started_at = parse_datetime(invocation.get("started_at"))
    completed_at = parse_datetime(invocation.get("completed_at"))
    return InvocationSample(
        index=index,
        idempotency_key=idempotency_key,
        status=str(invocation["status"]),
        invocation_id=str(invocation["id"]),
        accepted_latency_ms=accepted_latency_ms,
        end_to_end_latency_ms=end_to_end_latency_ms,
        queue_latency_ms=duration_ms(queued_at, started_at),
        execution_latency_ms=duration_ms(started_at, completed_at),
        error_type=invocation.get("error_type"),
        error_message=invocation.get("error_message"),
    )


def build_report(
    config: BenchmarkConfig,
    samples: list[InvocationSample],
    wall_duration_seconds: float,
) -> BenchmarkReport:
    return BenchmarkReport(
        generated_at=utc_now_iso(),
        host=socket.gethostname(),
        wall_duration_seconds=wall_duration_seconds,
        config=config,
        summary=summarize_samples(samples, wall_duration_seconds),
        samples=samples,
    )


def summarize_samples(
    samples: Sequence[InvocationSample],
    wall_duration_seconds: float,
) -> dict[str, Any]:
    statuses = Counter(sample.status for sample in samples)
    total = len(samples)
    succeeded = statuses.get("SUCCEEDED", 0)
    non_success = sum(statuses.get(status, 0) for status in NON_SUCCESS_STATUSES)

    end_to_end_values = metric_values(samples, "end_to_end_latency_ms")
    queue_values = metric_values(samples, "queue_latency_ms")
    execution_values = metric_values(samples, "execution_latency_ms")

    return {
        "total_invocations": total,
        "status_counts": dict(sorted(statuses.items())),
        "throughput_invocations_per_second": safe_rate(total, wall_duration_seconds),
        "success_rate": ratio(succeeded, total),
        "error_rate": ratio(non_success, total),
        "timeout_rate": ratio(statuses.get("TIMEOUT", 0), total),
        "p50_latency_ms": percentile(end_to_end_values, 0.50),
        "p95_latency_ms": percentile(end_to_end_values, 0.95),
        "p99_latency_ms": percentile(end_to_end_values, 0.99),
        "average_queue_latency_ms": average(queue_values),
        "average_execution_latency_ms": average(execution_values),
        "average_accept_latency_ms": average(metric_values(samples, "accepted_latency_ms")),
    }


def metric_values(samples: Sequence[InvocationSample], field_name: str) -> list[float]:
    values = [getattr(sample, field_name) for sample in samples]
    return sorted(value for value in values if value is not None)


def safe_rate(numerator: int, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 2)


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def average(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    if not values:
        return None
    index = max(0, ceil(len(values) * percentile_value) - 1)
    return round(sorted(values)[index], 2)


def render_markdown_report(report: BenchmarkReport) -> str:
    config = report.config
    summary = report.summary
    status_rows = "\n".join(
        f"| {status} | {count} |" for status, count in summary["status_counts"].items()
    )
    if not status_rows:
        status_rows = "| none | 0 |"

    return "\n".join(
        [
            "# Benchmark Report",
            "",
            "Generated by `python3 benchmarks/run_benchmark.py`.",
            "",
            "## Environment",
            "",
            f"- Generated at: {report.generated_at}",
            f"- Host: {report.host}",
            f"- API URL: {config.api_url}",
            f"- Workload: {config.workload}",
            f"- Function name: {config.function_name}",
            f"- Invocations: {config.invocations}",
            f"- Concurrent clients: {config.concurrency}",
            f"- Function timeout seconds: {config.timeout_seconds}",
            f"- Wall duration seconds: {report.wall_duration_seconds}",
            "",
            "## Results",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            metric_row(summary, "throughput_invocations_per_second"),
            metric_row(summary, "success_rate"),
            metric_row(summary, "error_rate"),
            metric_row(summary, "timeout_rate"),
            metric_row(summary, "p50_latency_ms"),
            metric_row(summary, "p95_latency_ms"),
            metric_row(summary, "p99_latency_ms"),
            metric_row(summary, "average_queue_latency_ms"),
            metric_row(summary, "average_execution_latency_ms"),
            metric_row(summary, "average_accept_latency_ms"),
            "",
            "## Status Counts",
            "",
            "| Status | Count |",
            "| --- | --- |",
            status_rows,
            "",
            "## Failure-Injection Evidence",
            "",
            "Run the crash-recovery regression test with:",
            "",
            "```bash",
            "python3 -m pytest tests/failure_injection/test_worker_crash_recovery.py",
            "```",
            "",
            "That test models a worker crash after a Redis Streams delivery but before ACK. "
            "The next worker reclaims the pending message, marks the stale worker offline, "
            "records the lost attempt as failed, and completes a new attempt.",
            "",
        ]
    )


def metric_row(summary: Mapping[str, Any], key: str) -> str:
    return f"| {key} | {summary[key]} |"


def write_report(report: BenchmarkReport, *, report_path: Path, json_output_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown_report(report), encoding="utf-8")
    json_output_path.write_text(json.dumps(report_to_dict(report), indent=2), encoding="utf-8")


def report_to_dict(report: BenchmarkReport) -> dict[str, Any]:
    data = asdict(report)
    return data


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def duration_ms(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    if started_at is None or completed_at is None:
        return None
    return round(max(0.0, (completed_at - started_at).total_seconds() * 1000), 2)


def elapsed_perf_ms(started: float, completed: float) -> float:
    return round(max(0.0, (completed - started) * 1000), 2)


def quote_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_for_key() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def main() -> None:
    args = parse_args()
    config = build_config(args)
    report = run_benchmark(config)
    write_report(report, report_path=args.report_path, json_output_path=args.json_output_path)
    print(render_markdown_report(report))
    print(f"Wrote markdown report to {args.report_path}")
    print(f"Wrote JSON report to {args.json_output_path}")


if __name__ == "__main__":
    main()
