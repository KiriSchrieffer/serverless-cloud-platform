"""Worker-side invocation state transitions."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus
from backend.app.models.invocation import Invocation, InvocationAttempt
from worker.app.queue.consumer import InvocationTask
from worker.app.runtime.docker_executor import RuntimeExecutionResult

TERMINAL_INVOCATION_STATUSES = {
    InvocationStatus.SUCCEEDED,
    InvocationStatus.FAILED,
    InvocationStatus.TIMEOUT,
    InvocationStatus.CANCELED,
}

ATTEMPT_TO_INVOCATION_STATUS = {
    InvocationAttemptStatus.SUCCEEDED: InvocationStatus.SUCCEEDED,
    InvocationAttemptStatus.FAILED: InvocationStatus.FAILED,
    InvocationAttemptStatus.TIMEOUT: InvocationStatus.TIMEOUT,
}


class InvocationCannotStartError(Exception):
    def __init__(self, invocation_id: UUID, status: InvocationStatus | None) -> None:
        super().__init__(f"Invocation {invocation_id} cannot start from status {status}")
        self.invocation_id = invocation_id
        self.status = status


class InvocationCannotCompleteError(Exception):
    def __init__(
        self,
        invocation_id: UUID,
        attempt_id: UUID,
        invocation_status: InvocationStatus | None,
        attempt_status: InvocationAttemptStatus | None,
    ) -> None:
        message = (
            f"Invocation {invocation_id} attempt {attempt_id} cannot complete from "
            f"invocation status {invocation_status} and attempt status {attempt_status}"
        )
        super().__init__(message)
        self.invocation_id = invocation_id
        self.attempt_id = attempt_id
        self.invocation_status = invocation_status
        self.attempt_status = attempt_status


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

    async def mark_terminal(
        self,
        invocation_id: UUID,
        attempt_id: UUID,
        execution_result: RuntimeExecutionResult,
    ) -> InvocationAttempt:
        invocation = await self.session.get(Invocation, invocation_id)
        attempt = await self.session.get(InvocationAttempt, attempt_id)

        if (
            invocation is None
            or attempt is None
            or attempt.invocation_id != invocation_id
            or invocation.status != InvocationStatus.RUNNING
            or attempt.status != InvocationAttemptStatus.RUNNING
        ):
            raise InvocationCannotCompleteError(
                invocation_id=invocation_id,
                attempt_id=attempt_id,
                invocation_status=invocation.status if invocation is not None else None,
                attempt_status=attempt.status if attempt is not None else None,
            )

        completed_at = self.utcnow()
        terminal_status = ATTEMPT_TO_INVOCATION_STATUS[execution_result.status]

        invocation.status = terminal_status
        invocation.completed_at = completed_at
        invocation.result_inline = execution_result.result_inline
        invocation.result_ref = execution_result.result_ref
        invocation.error_type = execution_result.error_type
        invocation.error_message = execution_result.error_message

        attempt.status = execution_result.status
        attempt.completed_at = completed_at
        if execution_result.duration_ms is None:
            attempt.duration_ms = self.duration_ms(attempt.started_at, completed_at)
        else:
            attempt.duration_ms = execution_result.duration_ms
        attempt.logs_ref = execution_result.logs_ref
        attempt.container_id = execution_result.container_id
        attempt.exit_code = execution_result.exit_code
        attempt.error_type = execution_result.error_type
        attempt.error_message = execution_result.error_message

        await self.session.commit()
        await self.session.refresh(attempt)
        return attempt

    @staticmethod
    def is_terminal_status(status: InvocationStatus | None) -> bool:
        return status in TERMINAL_INVOCATION_STATUSES

    @staticmethod
    def duration_ms(started_at: datetime, completed_at: datetime) -> int:
        return max(0, int((completed_at - started_at).total_seconds() * 1000))

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
