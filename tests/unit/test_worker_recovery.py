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
from worker.app.queue.consumer import InvocationTask
from worker.app.runtime.docker_executor import RuntimeExecutionResult
from worker.app.services.recovery import StaleWorkerRecoveryService

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class ClaimingInvocationTaskConsumer:
    def __init__(self, tasks: list[InvocationTask]) -> None:
        self.tasks = tasks
        self.claim_calls = []
        self.read_new_calls = 0
        self.acked_message_ids: list[str] = []

    async def claim_tasks_from_consumers(
        self,
        *,
        consumer_names: list[str],
        min_idle_ms: int,
        count: int,
    ) -> list[InvocationTask]:
        self.claim_calls.append(
            {
                "consumer_names": consumer_names,
                "min_idle_ms": min_idle_ms,
                "count": count,
            }
        )
        return self.tasks

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000):
        self.read_new_calls += 1
        return []

    async def acknowledge(self, task) -> int:
        self.acked_message_ids.append(task.stream_message_id)
        return 1


class FakeRuntimeExecutor:
    def __init__(self, result=None) -> None:
        self.result = result or RuntimeExecutionResult.succeeded({"ok": True})
        self.tasks = []

    async def execute(self, task):
        self.tasks.append(task)
        return self.result


class PaginatedClaimingConsumer:
    def __init__(self, pages: list[list[InvocationTask]]) -> None:
        self.pages = pages
        self.claim_calls: list[dict[str, int | list[str]]] = []
        self.read_new_calls = 0
        self.acked_message_ids: list[str] = []

    async def claim_tasks_from_consumers(
        self,
        *,
        consumer_names: list[str],
        min_idle_ms: int,
        count: int,
    ) -> list[InvocationTask]:
        self.claim_calls.append(
            {
                "consumer_names": consumer_names,
                "min_idle_ms": min_idle_ms,
                "count": count,
            }
        )
        return self.pages.pop(0)

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000):
        self.read_new_calls += 1
        return []

    async def acknowledge(self, task: InvocationTask) -> int:
        self.acked_message_ids.append(task.stream_message_id)
        return 1


@pytest.mark.asyncio
async def test_recovery_marks_stale_worker_offline_and_invocation_retrying(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        worker = create_worker(
            last_heartbeat=current_time() - timedelta(seconds=60),
            consumer_name="stale-consumer",
        )
        session.add(worker)
        await session.flush()
        invocation = await create_running_invocation(session, worker=worker, attempt_count=1)

        summary = await StaleWorkerRecoveryService(session).recover_stale_workers(
            stale_after_seconds=15,
            max_attempts=3,
        )

        refreshed_worker = await session.get(Worker, worker.id)
        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)

        assert summary.stale_worker_ids == [worker.id]
        assert summary.stale_consumer_names == ["stale-consumer"]
        assert summary.retried_invocation_ids == [invocation.id]
        assert summary.failed_invocation_ids == []
        assert refreshed_worker is not None
        assert refreshed_worker.status == WorkerStatus.RUNNING
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.RETRYING
        assert refreshed_invocation.started_at is None
        assert refreshed_invocation.error_type == "WorkerLostError"
        assert attempt.status == InvocationAttemptStatus.FAILED
        assert attempt.error_type == "WorkerLostError"

        repeated_summary = await StaleWorkerRecoveryService(session).recover_stale_workers(
            stale_after_seconds=15,
            max_attempts=3,
        )
        assert repeated_summary.stale_worker_ids == [worker.id]
        assert repeated_summary.stale_consumer_names == ["stale-consumer"]
        assert repeated_summary.recovered_invocation_count == 0

        await StaleWorkerRecoveryService(session).mark_workers_offline([worker.id])
        finalized_worker = await session.get(Worker, worker.id)
        assert finalized_worker is not None
        assert finalized_worker.status == WorkerStatus.OFFLINE
        assert finalized_worker.active_invocations == 0


@pytest.mark.asyncio
async def test_recovery_fails_invocation_when_attempts_are_exhausted(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        worker = create_worker(last_heartbeat=current_time() - timedelta(seconds=60))
        session.add(worker)
        await session.flush()
        invocation = await create_running_invocation(session, worker=worker, attempt_count=3)

        summary = await StaleWorkerRecoveryService(session).recover_stale_workers(
            stale_after_seconds=15,
            max_attempts=3,
        )

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert summary.retried_invocation_ids == []
        assert summary.failed_invocation_ids == [invocation.id]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.FAILED
        assert refreshed_invocation.completed_at is not None
        assert refreshed_invocation.error_type == "WorkerLostError"


@pytest.mark.asyncio
async def test_recovery_ignores_fresh_workers(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        worker = create_worker(last_heartbeat=current_time())
        session.add(worker)
        await session.flush()
        invocation = await create_running_invocation(session, worker=worker, attempt_count=1)

        summary = await StaleWorkerRecoveryService(session).recover_stale_workers(
            stale_after_seconds=15,
            max_attempts=3,
        )

        refreshed_worker = await session.get(Worker, worker.id)
        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)
        assert summary.recovered_invocation_count == 0
        assert refreshed_worker is not None
        assert refreshed_worker.status == WorkerStatus.RUNNING
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.RUNNING
        assert attempt.status == InvocationAttemptStatus.RUNNING


@pytest.mark.asyncio
async def test_process_worker_once_recovers_and_processes_claimed_pending_task(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        stale_worker = create_worker(
            last_heartbeat=current_time() - timedelta(seconds=60),
            consumer_name="stale-consumer",
        )
        current_worker = create_worker(last_heartbeat=current_time())
        session.add_all([stale_worker, current_worker])
        await session.flush()
        invocation = await create_running_invocation(
            session,
            worker=stale_worker,
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
        recovered=True,
    )
    consumer = ClaimingInvocationTaskConsumer([task])
    runtime = FakeRuntimeExecutor(RuntimeExecutionResult.succeeded({"recovered": True}))

    processed = await process_worker_once(
        test_sessionmaker,
        consumer,
        runtime_executor_factory=lambda session: runtime,
        worker_id=current_worker.id,
        stale_worker_seconds=15,
        max_attempts=3,
        pending_message_claim_count=5,
    )

    async with test_sessionmaker() as session:
        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempts = await get_attempts(session)

    assert processed == 1
    assert consumer.claim_calls == [
        {
            "consumer_names": ["stale-consumer"],
            "min_idle_ms": 0,
            "count": 2,
        }
    ]
    assert consumer.read_new_calls == 0
    assert consumer.acked_message_ids == [task.stream_message_id]
    assert runtime.tasks == [task]
    assert refreshed_invocation is not None
    assert refreshed_invocation.status == InvocationStatus.SUCCEEDED
    assert refreshed_invocation.result_inline == {"recovered": True}
    assert [(attempt.attempt_number, attempt.status) for attempt in attempts] == [
        (1, InvocationAttemptStatus.FAILED),
        (2, InvocationAttemptStatus.SUCCEEDED),
    ]
    assert attempts[1].worker_id == current_worker.id


@pytest.mark.asyncio
async def test_process_worker_once_drains_all_paginated_pending_tasks(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        stale_worker = create_worker(
            last_heartbeat=current_time() - timedelta(seconds=60),
            consumer_name="stale-consumer",
        )
        current_worker = create_worker(last_heartbeat=current_time())
        session.add_all([stale_worker, current_worker])
        await session.flush()
        invocations = [
            await create_running_invocation(session, worker=stale_worker, attempt_count=1)
            for _ in range(3)
        ]

    tasks = [
        make_recovery_task(invocation, index=index)
        for index, invocation in enumerate(invocations)
    ]
    consumer = PaginatedClaimingConsumer(
        [tasks[:2], tasks[2:]]
    )
    runtime = FakeRuntimeExecutor(RuntimeExecutionResult.succeeded({"recovered": True}))

    processed = await process_worker_once(
        test_sessionmaker,
        consumer,
        runtime_executor_factory=lambda _session: runtime,
        worker_id=current_worker.id,
        stale_worker_seconds=15,
        max_attempts=3,
        max_concurrency=2,
        pending_message_claim_count=10,
    )

    async with test_sessionmaker() as session:
        recovered_invocations = list(
            await session.scalars(
                select(Invocation).where(
                    Invocation.id.in_([invocation.id for invocation in invocations])
                )
            )
        )
        attempts = list(await session.scalars(select(InvocationAttempt)))

    assert processed == 3
    assert consumer.claim_calls == [
        {
            "consumer_names": ["stale-consumer"],
            "min_idle_ms": 0,
            "count": 2,
        },
        {
            "consumer_names": ["stale-consumer"],
            "min_idle_ms": 0,
            "count": 2,
        },
    ]
    assert consumer.read_new_calls == 0
    assert set(consumer.acked_message_ids) == {
        task.stream_message_id for task in tasks
    }
    assert all(
        invocation.status == InvocationStatus.SUCCEEDED
        for invocation in recovered_invocations
    )
    assert len(attempts) == 6


def create_worker(
    *,
    last_heartbeat: datetime,
    consumer_name: str | None = None,
) -> Worker:
    return Worker(
        hostname=f"worker-{uuid4().hex}",
        consumer_name=consumer_name,
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
    user = await session.get(User, OWNER_ID)
    if user is None:
        user = User(
            id=OWNER_ID,
            email=f"recovery-{uuid4().hex}@example.local",
            password_hash="development-only",
        )
        session.add(user)
    function = Function(owner_id=OWNER_ID, name=f"hello-{uuid4().hex}")
    session.add(function)
    await session.flush()

    version = FunctionVersion(
        function_id=function.id,
        version_number=1,
        runtime="python3.11",
        handler="main.handler",
        package_uri="storage/packages/hello/v1/function.zip",
        package_hash="0123456789abcdef0123456789abcdef",
        memory_limit_mb=256,
        cpu_limit=0.5,
        timeout_seconds=30,
    )
    session.add(version)
    await session.flush()

    queued_at = current_time() - timedelta(seconds=10)
    started_at = current_time() - timedelta(seconds=5)
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


def make_recovery_task(invocation: Invocation, *, index: int) -> InvocationTask:
    return InvocationTask(
        stream_message_id=f"171000000000{index}-0",
        invocation_id=invocation.id,
        function_version_id=invocation.function_version_id,
        owner_id=invocation.owner_id,
        attempt_number=1,
        queued_at=invocation.queued_at,
        deadline_at=invocation.deadline_at,
        recovered=True,
    )


async def get_only_attempt(session: AsyncSession) -> InvocationAttempt:
    result = await session.scalars(select(InvocationAttempt))
    return result.one()


async def get_attempts(session: AsyncSession) -> list[InvocationAttempt]:
    result = await session.scalars(
        select(InvocationAttempt).order_by(InvocationAttempt.attempt_number)
    )
    return list(result)


def current_time() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
