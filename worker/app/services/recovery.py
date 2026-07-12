"""Stale worker detection and invocation recovery."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus, WorkerStatus
from backend.app.models.invocation import Invocation, InvocationAttempt
from backend.app.models.worker import Worker


@dataclass(frozen=True)
class RecoverySummary:
    stale_worker_ids: list[UUID]
    stale_consumer_names: list[str]
    retried_invocation_ids: list[UUID]
    failed_invocation_ids: list[UUID]

    @property
    def recovered_invocation_count(self) -> int:
        return len(self.retried_invocation_ids) + len(self.failed_invocation_ids)


class StaleWorkerRecoveryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recover_stale_workers(
        self,
        *,
        stale_after_seconds: int,
        max_attempts: int,
    ) -> RecoverySummary:
        now = self.utcnow()
        cutoff = now - timedelta(seconds=stale_after_seconds)
        stale_workers = await self.find_stale_workers(cutoff)
        stale_worker_ids = [worker.id for worker in stale_workers]
        stale_consumer_names = [
            worker.consumer_name
            for worker in stale_workers
            if worker.consumer_name is not None
        ]
        if not stale_worker_ids:
            return RecoverySummary(
                stale_worker_ids=[],
                stale_consumer_names=[],
                retried_invocation_ids=[],
                failed_invocation_ids=[],
            )

        retried_invocation_ids: list[UUID] = []
        failed_invocation_ids: list[UUID] = []
        running_attempts = await self.find_running_attempts(stale_worker_ids)
        for attempt in running_attempts:
            invocation = await self.session.get(Invocation, attempt.invocation_id)
            if invocation is None or invocation.status != InvocationStatus.RUNNING:
                continue

            attempt.status = InvocationAttemptStatus.FAILED
            attempt.completed_at = now
            attempt.duration_ms = self.duration_ms(attempt.started_at, now)
            attempt.error_type = "WorkerLostError"
            attempt.error_message = (
                f"Worker {attempt.worker_id} stopped heartbeating before ACK"
            )

            if invocation.attempt_count >= max_attempts:
                invocation.status = InvocationStatus.FAILED
                invocation.completed_at = now
                invocation.error_type = "WorkerLostError"
                invocation.error_message = "Invocation attempts exhausted after worker loss"
                failed_invocation_ids.append(invocation.id)
            else:
                invocation.status = InvocationStatus.RETRYING
                invocation.started_at = None
                invocation.error_type = "WorkerLostError"
                invocation.error_message = "Invocation will be retried after worker loss"
                retried_invocation_ids.append(invocation.id)

        await self.session.commit()
        return RecoverySummary(
            stale_worker_ids=stale_worker_ids,
            stale_consumer_names=stale_consumer_names,
            retried_invocation_ids=retried_invocation_ids,
            failed_invocation_ids=failed_invocation_ids,
        )

    async def mark_workers_offline(self, worker_ids: list[UUID]) -> None:
        if not worker_ids:
            return
        workers = await self.session.scalars(
            select(Worker)
            .where(Worker.id.in_(worker_ids))
            .with_for_update()
        )
        for worker in workers:
            worker.status = WorkerStatus.OFFLINE
            worker.active_invocations = 0
        await self.session.commit()

    async def find_stale_workers(self, cutoff: datetime) -> list[Worker]:
        result = await self.session.scalars(
            select(Worker)
            .where(
                Worker.last_heartbeat < cutoff,
                Worker.status != WorkerStatus.OFFLINE,
            )
            .order_by(Worker.last_heartbeat)
            .with_for_update(skip_locked=True)
        )
        return list(result)

    async def find_running_attempts(self, worker_ids: list[UUID]) -> list[InvocationAttempt]:
        result = await self.session.scalars(
            select(InvocationAttempt)
            .where(
                InvocationAttempt.worker_id.in_(worker_ids),
                InvocationAttempt.status == InvocationAttemptStatus.RUNNING,
            )
            .order_by(InvocationAttempt.started_at)
        )
        return list(result)

    @staticmethod
    def duration_ms(started_at: datetime, completed_at: datetime) -> int:
        return max(0, int((completed_at - started_at).total_seconds() * 1000))

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
