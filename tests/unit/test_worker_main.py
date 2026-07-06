import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ResponseError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationStatus, WorkerStatus
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation, InvocationAttempt
from backend.app.models.user import User
from worker.app.main import (
    process_worker_once,
    register_worker,
    run_heartbeat_loop,
    run_worker_loop,
)
from worker.app.queue.consumer import RedisStreamConsumer, parse_xreadgroup_response
from worker.app.runtime.docker_executor import RuntimeExecutionResult
from worker.app.services.heartbeat import WorkerRuntimeState

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeRedis:
    def __init__(self) -> None:
        self.xgroup_create_calls = []

    async def xgroup_create(self, **kwargs) -> None:
        self.xgroup_create_calls.append(kwargs)


class ExistingGroupRedis:
    async def xgroup_create(self, **kwargs) -> None:
        raise ResponseError("BUSYGROUP Consumer Group name already exists")


class FakeInvocationTaskConsumer:
    def __init__(self, tasks) -> None:
        self.tasks = tasks
        self.acked_message_ids: list[str] = []

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000):
        return self.tasks[:count]

    async def acknowledge(self, task) -> int:
        self.acked_message_ids.append(task.stream_message_id)
        return 1


class FailingInvocationTaskConsumer:
    def __init__(self) -> None:
        self.read_calls = 0

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000):
        self.read_calls += 1
        raise RuntimeError("redis unavailable")

    async def acknowledge(self, task) -> int:
        raise AssertionError("unexpected acknowledge")


class FakeRuntimeExecutor:
    def __init__(self, result=None) -> None:
        self.result = result or RuntimeExecutionResult.succeeded({"ok": True})
        self.tasks = []

    async def execute(self, task):
        self.tasks.append(task)
        return self.result


@pytest.mark.asyncio
async def test_redis_stream_consumer_creates_group_with_mkstream() -> None:
    redis = FakeRedis()
    consumer = RedisStreamConsumer(
        redis=redis,
        stream_name="invocations",
        consumer_group="workers",
        consumer_name="worker-1",
    )

    await consumer.ensure_consumer_group()

    assert redis.xgroup_create_calls == [
        {
            "name": "invocations",
            "groupname": "workers",
            "id": "0",
            "mkstream": True,
        }
    ]


@pytest.mark.asyncio
async def test_redis_stream_consumer_ignores_existing_group() -> None:
    consumer = RedisStreamConsumer(
        redis=ExistingGroupRedis(),
        stream_name="invocations",
        consumer_group="workers",
        consumer_name="worker-1",
    )

    await consumer.ensure_consumer_group()


@pytest.mark.asyncio
async def test_process_worker_once_wires_session_processor_and_runtime(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session)

    worker = await register_worker(
        test_sessionmaker,
        hostname="worker-host",
        max_concurrency=2,
    )
    task = make_task(invocation)
    consumer = FakeInvocationTaskConsumer([task])
    runtime = FakeRuntimeExecutor(RuntimeExecutionResult.succeeded({"done": True}))
    runtime_state = WorkerRuntimeState()

    processed = await process_worker_once(
        test_sessionmaker,
        consumer,
        runtime_executor_factory=lambda session: runtime,
        worker_id=worker.id,
        runtime_state=runtime_state,
    )

    async with test_sessionmaker() as session:
        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)

    assert processed == 1
    assert consumer.acked_message_ids == [task.stream_message_id]
    assert runtime.tasks == [task]
    assert runtime_state.active_invocations == 0
    assert runtime_state.status == WorkerStatus.IDLE
    assert attempt.worker_id == worker.id
    assert refreshed_invocation is not None
    assert refreshed_invocation.status == InvocationStatus.SUCCEEDED
    assert refreshed_invocation.result_inline == {"done": True}


@pytest.mark.asyncio
async def test_run_heartbeat_loop_records_runtime_state(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    worker = await register_worker(
        test_sessionmaker,
        hostname="worker-host",
        max_concurrency=2,
    )
    runtime_state = WorkerRuntimeState()
    runtime_state.mark_task_started()
    stop_event = asyncio.Event()

    iterations = await run_heartbeat_loop(
        test_sessionmaker,
        worker_id=worker.id,
        runtime_state=runtime_state,
        stop_event=stop_event,
        interval_seconds=60,
        max_iterations=1,
    )

    async with test_sessionmaker() as session:
        refreshed_worker = await session.get(type(worker), worker.id)

    assert iterations == 1
    assert refreshed_worker is not None
    assert refreshed_worker.status == WorkerStatus.RUNNING
    assert refreshed_worker.active_invocations == 1


@pytest.mark.asyncio
async def test_run_worker_loop_logs_iteration_errors_and_continues(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    consumer = FailingInvocationTaskConsumer()
    runtime = FakeRuntimeExecutor()

    processed = await run_worker_loop(
        test_sessionmaker,
        consumer,
        runtime_executor_factory=lambda session: runtime,
        max_iterations=1,
        retry_sleep_seconds=0,
    )

    assert processed == 0
    assert consumer.read_calls == 1
    assert runtime.tasks == []


async def create_invocation(session: AsyncSession) -> Invocation:
    user = User(
        id=OWNER_ID,
        email=f"worker-main-{uuid4().hex}@example.local",
        password_hash="development-only",
    )
    function = Function(owner_id=OWNER_ID, name=f"hello-{uuid4().hex}")
    session.add_all([user, function])
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

    queued_at = datetime(2026, 7, 2, 10, 0, 0)
    invocation = Invocation(
        owner_id=OWNER_ID,
        function_version_id=version.id,
        status=InvocationStatus.QUEUED,
        payload_inline={},
        queued_at=queued_at,
        deadline_at=queued_at + timedelta(seconds=30),
        attempt_count=0,
    )
    session.add(invocation)
    await session.commit()
    await session.refresh(invocation)
    return invocation


def make_task(invocation: Invocation):
    return parse_xreadgroup_response(
        [
            (
                "invocations",
                [
                    (
                        "1710000000000-0",
                        {
                            "invocation_id": str(invocation.id),
                            "function_version_id": str(invocation.function_version_id),
                            "owner_id": str(invocation.owner_id),
                            "attempt_number": "1",
                            "queued_at": invocation.queued_at.isoformat(),
                            "deadline_at": invocation.deadline_at.isoformat(),
                        },
                    )
                ],
            )
        ]
    )[0]


async def get_only_attempt(session: AsyncSession) -> InvocationAttempt:
    result = await session.scalars(select(InvocationAttempt))
    return result.one()
