"""Worker-side invocation state transitions."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus
from backend.app.models.invocation import Invocation, InvocationAttempt
from worker.app.queue.consumer import InvocationTask


class InvocationCannotStartError(Exception):
    def __init__(self, invocation_id: UUID, status: InvocationStatus | None) -> None:
        super().__init__(f"Invocation {invocation_id} cannot start from status {status}")
        self.invocation_id = invocation_id
        self.status = status


class InvocationStateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def mark_running(
        self,
        task: InvocationTask,
        worker_id: UUID | None = None,
    ) -> InvocationAttempt:
        invocation = await self.session.get(Invocation, task.invocation_id)
        if invocation is None or invocation.status not in {
            InvocationStatus.QUEUED,
            InvocationStatus.RETRYING,
        }:
            raise InvocationCannotStartError(
                task.invocation_id,
                invocation.status if invocation is not None else None,
            )

        started_at = self.utcnow()
        attempt = InvocationAttempt(
            invocation_id=task.invocation_id,
            worker_id=worker_id,
            attempt_number=task.attempt_number,
            status=InvocationAttemptStatus.RUNNING,
            started_at=started_at,
        )
        invocation.status = InvocationStatus.RUNNING
        invocation.started_at = started_at
        invocation.attempt_count = max(invocation.attempt_count, task.attempt_number)

        self.session.add(attempt)
        await self.session.commit()
        await self.session.refresh(attempt)
        return attempt

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
