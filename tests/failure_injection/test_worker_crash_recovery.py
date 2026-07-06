from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus, WorkerStatus
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation, InvocationAttempt
from backend.app.models.user import User
from backend.app.models.worker import Worker
from worker.app.main import process_worker_once
from worker.app.queue.consumer import ClaimedInvocationTasks, InvocationTask
from worker.app.runtime.docker_executor import RuntimeExecutionResult

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class PendingOnlyConsumer:
    def __init__(self, task: InvocationTask) -> None:
        self.task = task
        self.claim_calls: list[dict[str, int]] = []
        self.read_new_calls = 0
        self.acked_message_ids: list[str] = []

    async def claim_stale_tasks(self, *, min_idle_ms: int, count: int) -> ClaimedInvocationTasks:
        self.claim_calls.append({"min_idle_ms": min_idle_ms, "count": count})
        return ClaimedInvocationTasks(next_start_id="0-0", tasks=[self.task])

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000) -> list[InvocationTask]:
        self.read_new_calls += 1
        return []

    async def acknowledge(self, task: InvocationTask) -> int:
        self.acked_message_ids.append(task.stream_message_id)
        return 1


class SuccessfulRuntime:
    def __init__(self) -> None:
        self.tasks: list[InvocationTask] = []

    async def execute(self, task: InvocationTask) -> RuntimeExecutionResult:
        self.tasks.append(task)
        return RuntimeExecutionResult.succeeded({"recovered": True}, duration_ms=25)


@pytest.mark.asyncio
async def test_pending_invocation_is_reclaimed_after_worker_crash(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        crashed_worker = make_worker(
            hostname="crashed-worker",
            last_heartbeat=current_time() - timedelta(seconds=60),
        )
        current_worker = make_worker(hostname="current-worker", last_heartbeat=current_time())
        session.add_all([crashed_worker, current_worker])
        await session.flush()
        invocation = await create_running_invocation(
            session,
            worker=crashed_worker,
            attempt_count=1,
        )

    task = InvocationTask(
        stream_message_id="1710000000000-0",
        invocation_id=invocation.id,
        function_version_id=invocation.function_version_id,
        owner_id=invocation.owner_id,
        attempt_number=1,
        queued_at=invocation.queued_at,
        deadline_at=invocation.deadline_at,
    )
    consumer = PendingOnlyConsumer(task)
    runtime = SuccessfulRuntime()

    processed = await process_worker_once(
        test_sessionmaker,
        consumer,
        runtime_executor_factory=lambda _session: runtime,
        worker_id=current_worker.id,
        stale_worker_seconds=15,
        max_attempts=3,
        pending_message_claim_count=5,
    )

    async with test_sessionmaker() as session:
        recovered_invocation = await session.get(Invocation, invocation.id)
        attempts = list(
            (
                await session.scalars(
                    select(InvocationAttempt).order_by(InvocationAttempt.attempt_number)
                )
            ).all()
        )
        recovered_crashed_worker = await session.get(Worker, crashed_worker.id)

    assert processed == 1
    assert consumer.claim_calls == [{"min_idle_ms": 15_000, "count": 5}]
    assert consumer.read_new_calls == 0
    assert consumer.acked_message_ids == [task.stream_message_id]
    assert runtime.tasks == [task]
    assert recovered_crashed_worker is not None
    assert recovered_crashed_worker.status == WorkerStatus.OFFLINE
    assert recovered_invocation is not None
    assert recovered_invocation.status == InvocationStatus.SUCCEEDED
    assert recovered_invocation.result_inline == {"recovered": True}
    assert [(attempt.attempt_number, attempt.status) for attempt in attempts] == [
        (1, InvocationAttemptStatus.FAILED),
        (2, InvocationAttemptStatus.SUCCEEDED),
    ]
    assert attempts[1].worker_id == current_worker.id


def make_worker(*, hostname: str, last_heartbeat: datetime) -> Worker:
    return Worker(
        hostname=f"{hostname}-{uuid4().hex}",
        status=WorkerStatus.RUNNING,
        last_heartbeat=last_heartbeat,
        active_invocations=1,
        max_concurrency=2,
        started_at=last_heartbeat,
    )


async def create_running_invocation(
    session: AsyncSession,
    *,
    worker: Worker,
    attempt_count: int,
) -> Invocation:
    user = User(
        id=OWNER_ID,
        email=f"failure-injection-{uuid4().hex}@example.local",
        password_hash="development-only",
    )
    function = Function(owner_id=OWNER_ID, name=f"crash-recovery-{uuid4().hex}")
    session.add_all([user, function])
    await session.flush()

    version = FunctionVersion(
        function_id=function.id,
        version_number=1,
        runtime="python3.11",
        handler="main.handler",
        package_uri="storage/packages/crash-recovery/v1/function.zip",
        package_hash="0123456789abcdef0123456789abcdef",
        memory_limit_mb=256,
        cpu_limit=0.5,
        timeout_seconds=30,
    )
    session.add(version)
    await session.flush()

    queued_at = datetime(2026, 7, 6, 9, 59, 30)
    started_at = datetime(2026, 7, 6, 9, 59, 45)
    invocation = Invocation(
        owner_id=OWNER_ID,
        function_version_id=version.id,
        status=InvocationStatus.RUNNING,
        payload_inline={},
        queued_at=queued_at,
        started_at=started_at,
        deadline_at=queued_at + timedelta(seconds=30),
        attempt_count=attempt_count,
    )
    session.add(invocation)
    await session.flush()

    session.add(
        InvocationAttempt(
            invocation_id=invocation.id,
            worker_id=worker.id,
            attempt_number=attempt_count,
            status=InvocationAttemptStatus.RUNNING,
            started_at=started_at,
        )
    )
    await session.commit()
    await session.refresh(invocation)
    return invocation


def current_time() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
