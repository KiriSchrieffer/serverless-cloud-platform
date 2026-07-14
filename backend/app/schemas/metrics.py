"""Metrics summary and latency percentile schemas."""

from pydantic import BaseModel


class InvocationMetricsSummary(BaseModel):
    total: int
    terminal: int
    queued: int
    running: int
    retrying: int
    succeeded: int
    failed: int
    timeout: int
    canceled: int
    success_rate: float
    error_rate: float
    retry_count: int
    throughput_per_minute: float
    average_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    p99_latency_ms: float | None
    average_execution_ms: float | None
    p95_execution_ms: float | None


class WorkerMetricsSummary(BaseModel):
    total: int
    active: int
    stale: int
    offline: int
    active_invocations: int


class QueueMetricsSummary(BaseModel):
    depth: int
    oldest_age_seconds: int | None
    pending_dispatches: int
    oldest_dispatch_age_seconds: int | None


class MetricsSummary(BaseModel):
    invocations: InvocationMetricsSummary
    queue: QueueMetricsSummary
    workers: WorkerMetricsSummary
