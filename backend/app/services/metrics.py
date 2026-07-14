"""Dashboard metrics aggregation service."""

from datetime import UTC, datetime, timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import InvocationStatus, WorkerStatus
from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.invocation import Invocation, InvocationAttempt
from backend.app.models.worker import Worker
from backend.app.schemas.metrics import (
    InvocationMetricsSummary,
    MetricsSummary,
    QueueMetricsSummary,
    WorkerMetricsSummary,
)
from backend.app.schemas.worker import WorkerRead


class PlatformMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_workers(self, *, stale_after_seconds: int) -> list[WorkerRead]:
        now = self.utcnow()
        result = await self.session.scalars(
            select(Worker).order_by(Worker.status, Worker.hostname, Worker.started_at)
        )
        return [
            self.worker_read_model(
                worker=worker,
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
            for worker in result
        ]

    async def get_summary(
        self,
        *,
        owner_id: UUID,
        stale_after_seconds: int,
    ) -> MetricsSummary:
        now = self.utcnow()
        return MetricsSummary(
            invocations=await self.get_invocation_summary(owner_id, now=now),
            queue=await self.get_queue_summary(owner_id, now=now),
            workers=await self.get_worker_summary(stale_after_seconds),
        )

    async def get_invocation_summary(
        self,
        owner_id: UUID,
        *,
        now: datetime,
    ) -> InvocationMetricsSummary:
        status_counts = await self.get_invocation_status_counts(owner_id)
        total = sum(status_counts.values())
        succeeded = status_counts[InvocationStatus.SUCCEEDED]
        errors = sum(
            status_counts[status]
            for status in (
                InvocationStatus.FAILED,
                InvocationStatus.TIMEOUT,
                InvocationStatus.CANCELED,
            )
        )
        terminal = succeeded + errors
        execution_durations = await self.get_execution_durations(owner_id)
        invocation_durations = await self.get_invocation_durations(owner_id)

        return InvocationMetricsSummary(
            total=total,
            terminal=terminal,
            queued=status_counts[InvocationStatus.QUEUED],
            running=status_counts[InvocationStatus.RUNNING],
            retrying=status_counts[InvocationStatus.RETRYING],
            succeeded=succeeded,
            failed=status_counts[InvocationStatus.FAILED],
            timeout=status_counts[InvocationStatus.TIMEOUT],
            canceled=status_counts[InvocationStatus.CANCELED],
            success_rate=round(succeeded / terminal, 4) if terminal else 0.0,
            error_rate=round(errors / terminal, 4) if terminal else 0.0,
            retry_count=await self.get_retry_count(owner_id),
            throughput_per_minute=await self.get_recent_throughput(owner_id, now=now),
            average_latency_ms=self.average(invocation_durations),
            p50_latency_ms=self.percentile(invocation_durations, 0.50),
            p95_latency_ms=self.percentile(invocation_durations, 0.95),
            p99_latency_ms=self.percentile(invocation_durations, 0.99),
            average_execution_ms=self.average(execution_durations),
            p95_execution_ms=self.percentile(execution_durations, 0.95),
        )

    async def get_queue_summary(self, owner_id: UUID, *, now: datetime) -> QueueMetricsSummary:
        queue_statuses = (InvocationStatus.QUEUED, InvocationStatus.RETRYING)
        depth, oldest_queued_at = (
            await self.session.execute(
                select(func.count(Invocation.id), func.min(Invocation.queued_at)).where(
                    Invocation.owner_id == owner_id,
                    Invocation.status.in_(queue_statuses),
                )
            )
        ).one()
        pending_dispatches, oldest_dispatch_at = (
            await self.session.execute(
                select(
                    func.count(InvocationDispatch.id),
                    func.min(InvocationDispatch.created_at),
                )
                .join(Invocation, InvocationDispatch.invocation_id == Invocation.id)
                .where(
                    Invocation.owner_id == owner_id,
                    InvocationDispatch.published_at.is_(None),
                )
            )
        ).one()
        return QueueMetricsSummary(
            depth=depth,
            oldest_age_seconds=self.age_seconds(oldest_queued_at, now=now),
            pending_dispatches=pending_dispatches,
            oldest_dispatch_age_seconds=self.age_seconds(oldest_dispatch_at, now=now),
        )

    async def get_invocation_status_counts(
        self,
        owner_id: UUID,
    ) -> dict[InvocationStatus, int]:
        counts = {status: 0 for status in InvocationStatus}
        result = await self.session.execute(
            select(Invocation.status, func.count())
            .where(Invocation.owner_id == owner_id)
            .group_by(Invocation.status)
        )
        for status, count in result.all():
            counts[status] = count
        return counts

    async def get_execution_durations(self, owner_id: UUID) -> list[int]:
        result = await self.session.scalars(
            select(InvocationAttempt.duration_ms)
            .join(Invocation, InvocationAttempt.invocation_id == Invocation.id)
            .where(
                Invocation.owner_id == owner_id,
                InvocationAttempt.completed_at.is_not(None),
                InvocationAttempt.duration_ms.is_not(None),
            )
        )
        return sorted(duration for duration in result if duration is not None)

    async def get_invocation_durations(self, owner_id: UUID) -> list[int]:
        result = await self.session.execute(
            select(Invocation.queued_at, Invocation.completed_at).where(
                Invocation.owner_id == owner_id,
                Invocation.completed_at.is_not(None),
            )
        )
        return sorted(
            max(0, int((completed_at - queued_at).total_seconds() * 1000))
            for queued_at, completed_at in result.all()
        )

    async def get_retry_count(self, owner_id: UUID) -> int:
        attempt_counts = await self.session.scalars(
            select(Invocation.attempt_count).where(Invocation.owner_id == owner_id)
        )
        return sum(max(0, attempt_count - 1) for attempt_count in attempt_counts)

    async def get_recent_throughput(self, owner_id: UUID, *, now: datetime) -> float:
        completed = await self.session.scalar(
            select(func.count(Invocation.id)).where(
                Invocation.owner_id == owner_id,
                Invocation.completed_at.is_not(None),
                Invocation.completed_at >= now - timedelta(minutes=1),
            )
        )
        return float(completed or 0)

    async def get_worker_summary(self, stale_after_seconds: int) -> WorkerMetricsSummary:
        now = self.utcnow()
        result = await self.session.scalars(select(Worker))
        workers = list(result)
        stale_workers = [
            worker
            for worker in workers
            if self.is_stale_worker(
                worker=worker,
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
        ]
        return WorkerMetricsSummary(
            total=len(workers),
            active=sum(
                1
                for worker in workers
                if worker.status != WorkerStatus.OFFLINE
                and worker.active_invocations > 0
            ),
            stale=len(stale_workers),
            offline=sum(1 for worker in workers if worker.status == WorkerStatus.OFFLINE),
            active_invocations=sum(worker.active_invocations for worker in workers),
        )

    def worker_read_model(
        self,
        *,
        worker: Worker,
        now: datetime,
        stale_after_seconds: int,
    ) -> WorkerRead:
        heartbeat_age_seconds = self.heartbeat_age_seconds(worker=worker, now=now)
        return WorkerRead(
            id=worker.id,
            hostname=worker.hostname,
            consumer_name=worker.consumer_name,
            status=worker.status,
            last_heartbeat=worker.last_heartbeat,
            heartbeat_age_seconds=heartbeat_age_seconds,
            stale=self.is_stale_worker(
                worker=worker,
                now=now,
                stale_after_seconds=stale_after_seconds,
            ),
            active_invocations=worker.active_invocations,
            max_concurrency=worker.max_concurrency,
            started_at=worker.started_at,
            created_at=worker.created_at,
            updated_at=worker.updated_at,
        )

    @staticmethod
    def is_stale_worker(
        *,
        worker: Worker,
        now: datetime,
        stale_after_seconds: int,
    ) -> bool:
        if worker.status == WorkerStatus.OFFLINE:
            return False
        return worker.last_heartbeat < now - timedelta(seconds=stale_after_seconds)

    @staticmethod
    def heartbeat_age_seconds(*, worker: Worker, now: datetime) -> int:
        return max(0, int((now - worker.last_heartbeat).total_seconds()))

    @staticmethod
    def average(values: list[int]) -> float | None:
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    @staticmethod
    def percentile(values: list[int], percentile: float) -> float | None:
        if not values:
            return None
        index = max(0, ceil(len(values) * percentile) - 1)
        return float(values[index])

    @staticmethod
    def age_seconds(value: datetime | None, *, now: datetime) -> int | None:
        if value is None:
            return None
        return max(0, int((now - value).total_seconds()))

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
