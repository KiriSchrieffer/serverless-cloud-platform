"""Metrics summary and latency percentile schemas."""

from pydantic import BaseModel


class InvocationMetricsSummary(BaseModel):
    total: int
    queued: int
    running: int
    retrying: int
    succeeded: int
    failed: int
    timeout: int
    canceled: int
    success_rate: float
    average_execution_ms: float | None
    p95_execution_ms: float | None


class WorkerMetricsSummary(BaseModel):
    total: int
    active: int
    stale: int
    offline: int
    active_invocations: int


class MetricsSummary(BaseModel):
    invocations: InvocationMetricsSummary
    workers: WorkerMetricsSummary
