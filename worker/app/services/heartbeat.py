"""Worker registration and heartbeat updates."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import WorkerStatus
from backend.app.models.worker import Worker


class WorkerNotFoundError(Exception):
    def __init__(self, worker_id: UUID) -> None:
        super().__init__(f"Worker not found: {worker_id}")
        self.worker_id = worker_id


@dataclass
class WorkerRuntimeState:
    active_invocations: int = 0
    status: WorkerStatus = WorkerStatus.IDLE

    def mark_task_started(self) -> None:
        self.active_invocations += 1
        self.status = WorkerStatus.RUNNING

    def mark_task_finished(self) -> None:
        self.active_invocations = max(0, self.active_invocations - 1)
        self.status = WorkerStatus.RUNNING if self.active_invocations else WorkerStatus.IDLE


class WorkerHeartbeatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register_worker(
        self,
        *,
        hostname: str,
        max_concurrency: int,
        consumer_name: str | None = None,
    ) -> Worker:
        now = self.utcnow()
        worker = Worker(
            hostname=hostname,
            consumer_name=consumer_name,
            status=WorkerStatus.IDLE,
            last_heartbeat=now,
            active_invocations=0,
            max_concurrency=max_concurrency,
            started_at=now,
        )
        self.session.add(worker)
        await self.session.commit()
        await self.session.refresh(worker)
        return worker

    async def record_heartbeat(
        self,
        worker_id: UUID,
        *,
        active_invocations: int,
        status: WorkerStatus,
    ) -> Worker:
        worker = await self.session.get(Worker, worker_id)
        if worker is None:
            raise WorkerNotFoundError(worker_id)

        worker.last_heartbeat = self.utcnow()
        worker.active_invocations = active_invocations
        worker.status = status
        await self.session.commit()
        await self.session.refresh(worker)
        return worker

    async def mark_offline(self, worker_id: UUID) -> Worker:
        worker = await self.session.get(Worker, worker_id)
        if worker is None:
            raise WorkerNotFoundError(worker_id)

        worker.last_heartbeat = self.utcnow()
        worker.active_invocations = 0
        worker.status = WorkerStatus.OFFLINE
        await self.session.commit()
        await self.session.refresh(worker)
        return worker

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
