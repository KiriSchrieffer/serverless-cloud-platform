from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus
from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation, InvocationAttempt
from backend.app.models.user import User
from worker.app.queue.consumer import RedisStreamConsumer, parse_xreadgroup_response
from worker.app.runtime.docker_executor import RuntimeExecutionResult
from worker.app.services.executor import WorkerTaskProcessor
from worker.app.services.invocation_state import (
    InvocationCannotStartError,
    InvocationStateService,
)
from worker.app.services.retry import RetryPolicy


OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeInvocationTaskConsumer:
    def __init__(self, tasks) -> None:
        self.tasks = tasks
        self.acked_message_ids: list[str] = []

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000):
        return self.tasks[:count]

    async def acknowledge(self, task) -> int:
        self.acked_message_ids.append(task.stream_message_id)
        return 1


class FakeRuntimeExecutor:
    def __init__(self, result=None, exception: Exception | None = None) -> None:
        self.result = result or RuntimeExecutionResult.succeeded({"ok": True})
        self.exception = exception
        self.tasks = []

    async def execute(self, task):
        self.tasks.append(task)
        if self.exception is not None:
            raise self.exception
        return self.result


class FakeRedisForClaim:
    def __init__(self, response) -> None:
        self.response = response
        self.xautoclaim_kwargs = None

    async def xautoclaim(self, **kwargs):
        self.xautoclaim_kwargs = kwargs
        return self.response


class FakeRedisForExactClaim:
    def __init__(self, message_id: bytes, fields: dict[bytes, bytes]) -> None:
        self.message_id = message_id
        self.fields = fields
        self.xpending_range_calls: list[dict] = []
        self.xclaim_calls: list[dict] = []

    async def xpending_range(self, **kwargs):
        self.xpending_range_calls.append(kwargs)
        if kwargs["consumername"] == "stale-consumer":
            return [{"message_id": self.message_id}]
        return []

    async def xclaim(self, **kwargs):
        self.xclaim_calls.append(kwargs)
        return [(self.message_id, self.fields)]


def test_parse_xreadgroup_response_decodes_invocation_task() -> None:
    invocation_id = uuid4()
    function_version_id = uuid4()
    queued_at = datetime(2026, 6, 20, 10, 0, 0)
    deadline_at = datetime(2026, 6, 20, 10, 0, 30)

    tasks = parse_xreadgroup_response(
        [
            (
                b"invocations",
                [
                    (
                        b"1710000000000-0",
                        {
                            b"invocation_id": str(invocation_id).encode(),
                            b"function_version_id": str(function_version_id).encode(),
                            b"owner_id": str(OWNER_ID).encode(),
                            b"attempt_number": b"1",
                            b"queued_at": queued_at.isoformat().encode(),
                            b"deadline_at": deadline_at.isoformat().encode(),
                        },
                    )
                ],
            )
        ]
    )

    assert len(tasks) == 1
    assert tasks[0].stream_message_id == "1710000000000-0"
    assert tasks[0].invocation_id == invocation_id
    assert tasks[0].function_version_id == function_version_id
    assert tasks[0].owner_id == OWNER_ID
    assert tasks[0].attempt_number == 1
    assert tasks[0].queued_at == queued_at
    assert tasks[0].deadline_at == deadline_at


@pytest.mark.asyncio
async def test_claim_stale_tasks_decodes_xautoclaim_response() -> None:
    invocation_id = uuid4()
    function_version_id = uuid4()
    queued_at = datetime(2026, 6, 20, 10, 0, 0)
    deadline_at = datetime(2026, 6, 20, 10, 0, 30)
    redis = FakeRedisForClaim(
        (
            b"0-0",
            [
                (
                    b"1710000000000-0",
                    {
                        b"invocation_id": str(invocation_id).encode(),
                        b"function_version_id": str(function_version_id).encode(),
                        b"owner_id": str(OWNER_ID).encode(),
                        b"attempt_number": b"1",
                        b"queued_at": queued_at.isoformat().encode(),
                        b"deadline_at": deadline_at.isoformat().encode(),
                    },
                )
            ],
            [],
        )
    )
    consumer = RedisStreamConsumer(
        redis=redis,
        stream_name="invocations",
        consumer_group="workers",
        consumer_name="worker-1",
    )

    claimed = await consumer.claim_stale_tasks(min_idle_ms=15_000, count=5)

    assert redis.xautoclaim_kwargs == {
        "name": "invocations",
        "groupname": "workers",
        "consumername": "worker-1",
        "min_idle_time": 15_000,
        "start_id": "0-0",
        "count": 5,
    }
    assert claimed.next_start_id == "0-0"
    assert len(claimed.tasks) == 1
    assert claimed.tasks[0].stream_message_id == "1710000000000-0"
    assert claimed.tasks[0].invocation_id == invocation_id
    assert claimed.tasks[0].function_version_id == function_version_id
    assert claimed.tasks[0].owner_id == OWNER_ID
    assert claimed.tasks[0].recovered is True


@pytest.mark.asyncio
async def test_claim_tasks_from_consumers_only_claims_named_stale_consumer() -> None:
    invocation_id = uuid4()
    function_version_id = uuid4()
    queued_at = datetime(2026, 6, 20, 10, 0, 0)
    deadline_at = datetime(2026, 6, 20, 10, 0, 30)
    message_id = b"1710000000000-0"
    fields = {
        b"invocation_id": str(invocation_id).encode(),
        b"function_version_id": str(function_version_id).encode(),
        b"owner_id": str(OWNER_ID).encode(),
        b"attempt_number": b"1",
        b"queued_at": queued_at.isoformat().encode(),
        b"deadline_at": deadline_at.isoformat().encode(),
    }
    redis = FakeRedisForExactClaim(message_id, fields)
    consumer = RedisStreamConsumer(
        redis=redis,
        stream_name="invocations",
        consumer_group="workers",
        consumer_name="current-consumer",
    )

    tasks = await consumer.claim_tasks_from_consumers(
        consumer_names=["stale-consumer", "fresh-consumer"],
        min_idle_ms=15_000,
        count=5,
    )

    assert [call["consumername"] for call in redis.xpending_range_calls] == [
        "stale-consumer",
        "fresh-consumer",
    ]
    assert redis.xclaim_calls == [
        {
            "name": "invocations",
            "groupname": "workers",
            "consumername": "current-consumer",
            "min_idle_time": 15_000,
            "message_ids": [message_id],
        }
    ]
    assert len(tasks) == 1
    assert tasks[0].invocation_id == invocation_id
    assert tasks[0].recovered is True
    assert tasks[0].recovered is True


@pytest.mark.asyncio
async def test_mark_running_creates_attempt_and_updates_invocation(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)

        attempt = await InvocationStateService(session).mark_running(task)

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.RUNNING
        assert refreshed_invocation.started_at == attempt.started_at
        assert refreshed_invocation.attempt_count == 1
        assert attempt.invocation_id == invocation.id
        assert attempt.attempt_number == 1
        assert attempt.worker_id is None


@pytest.mark.asyncio
async def test_mark_running_increments_attempt_number_for_retrying_invocation(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.RETRYING)
        invocation.attempt_count = 1
        await session.commit()
        task = replace(make_task(invocation), attempt_number=2)

        attempt = await InvocationStateService(session).mark_running(task)

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.RUNNING
        assert refreshed_invocation.attempt_count == 2
        assert attempt.attempt_number == 2


@pytest.mark.asyncio
async def test_mark_running_rejects_non_queued_invocation(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.SUCCEEDED)
        task = make_task(invocation)

        with pytest.raises(InvocationCannotStartError) as exc_info:
            await InvocationStateService(session).mark_running(task)

        assert exc_info.value.invocation_id == invocation.id
        assert exc_info.value.status == InvocationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_mark_terminal_records_success_result_and_attempt_details(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        service = InvocationStateService(session)
        attempt = await service.mark_running(task)

        completed_attempt = await service.mark_terminal(
            invocation_id=invocation.id,
            attempt_id=attempt.id,
            execution_result=RuntimeExecutionResult.succeeded(
                {"message": "hello"},
                duration_ms=37,
                logs_ref="storage/logs/invocation.log",
                container_id="container-123",
            ),
        )

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.SUCCEEDED
        assert refreshed_invocation.result_inline == {"message": "hello"}
        assert refreshed_invocation.error_type is None
        assert refreshed_invocation.completed_at == completed_attempt.completed_at
        assert completed_attempt.status == InvocationAttemptStatus.SUCCEEDED
        assert completed_attempt.duration_ms == 37
        assert completed_attempt.logs_ref == "storage/logs/invocation.log"
        assert completed_attempt.container_id == "container-123"
        assert completed_attempt.exit_code == 0


@pytest.mark.asyncio
async def test_worker_task_processor_executes_marks_succeeded_and_acks_message(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor(
            RuntimeExecutionResult.succeeded({"ok": True}, duration_ms=15)
        )
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert runtime.tasks == [task]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.SUCCEEDED
        assert refreshed_invocation.result_inline == {"ok": True}


@pytest.mark.asyncio
async def test_worker_task_processor_records_runtime_failure_and_acks_message(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor(
            RuntimeExecutionResult.failed(
                "ValueError",
                "invalid input",
                duration_ms=9,
                logs_ref="storage/logs/failure.log",
            )
        )
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.FAILED
        assert refreshed_invocation.error_type == "ValueError"
        assert refreshed_invocation.error_message == "invalid input"
        assert attempt.status == InvocationAttemptStatus.FAILED
        assert attempt.logs_ref == "storage/logs/failure.log"


@pytest.mark.asyncio
async def test_worker_task_processor_records_timeout_and_acks_message(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor(
            RuntimeExecutionResult.timed_out(
                "invocation exceeded deadline",
                duration_ms=30_000,
            )
        )
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.TIMEOUT
        assert refreshed_invocation.error_type == "TimeoutError"
        assert refreshed_invocation.error_message == "invocation exceeded deadline"
        assert attempt.status == InvocationAttemptStatus.TIMEOUT
        assert attempt.duration_ms == 30_000


@pytest.mark.asyncio
async def test_worker_task_processor_converts_runtime_exception_to_failure(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor(exception=RuntimeError("container crashed"))
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.FAILED
        assert refreshed_invocation.error_type == "RuntimeError"
        assert refreshed_invocation.error_message == "container crashed"


@pytest.mark.asyncio
async def test_worker_task_processor_schedules_retry_and_acks_original_message(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor(exception=RuntimeError("docker unavailable"))
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
            retry_policy=RetryPolicy(max_attempts=3, jitter=lambda _upper: 0.0),
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)
        retry_dispatch = await session.scalar(
            select(InvocationDispatch).where(
                InvocationDispatch.invocation_id == invocation.id,
                InvocationDispatch.attempt_number == 2,
            )
        )
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.RETRYING
        assert refreshed_invocation.completed_at is None
        assert refreshed_invocation.started_at is None
        assert refreshed_invocation.error_type == "RuntimeError"
        assert refreshed_invocation.error_message == "docker unavailable"
        assert attempt.status == InvocationAttemptStatus.FAILED
        assert attempt.error_type == "RuntimeError"
        assert retry_dispatch is not None
        assert retry_dispatch.available_at == attempt.completed_at + timedelta(seconds=1)
        assert retry_dispatch.published_at is None


@pytest.mark.asyncio
async def test_worker_task_processor_acks_retryable_failure_when_attempts_exhausted(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        invocation.attempt_count = 2
        await session.commit()
        task = replace(make_task(invocation), attempt_number=3)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor(exception=RuntimeError("docker unavailable"))
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
            retry_policy=RetryPolicy(max_attempts=3),
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempt = await get_only_attempt(session)
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.FAILED
        assert refreshed_invocation.error_type == "RuntimeError"
        assert attempt.status == InvocationAttemptStatus.FAILED


@pytest.mark.asyncio
async def test_worker_task_processor_acks_already_terminal_invocation_without_execution(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.SUCCEEDED)
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor()
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert runtime.tasks == []


@pytest.mark.asyncio
async def test_worker_task_processor_acks_obsolete_recovered_attempt(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.RETRYING)
        invocation.attempt_count = 1
        session.add(
            InvocationDispatch(
                invocation_id=invocation.id,
                attempt_number=2,
                created_at=current_time(),
                available_at=current_time() + timedelta(seconds=1),
            )
        )
        await session.commit()
        task = replace(make_task(invocation), recovered=True)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor()
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        attempts = list(await session.scalars(select(InvocationAttempt)))
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert runtime.tasks == []
        assert attempts == []


@pytest.mark.asyncio
async def test_worker_task_processor_acks_duplicate_running_attempt(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.RUNNING)
        invocation.attempt_count = 1
        await session.commit()
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor()
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert runtime.tasks == []


@pytest.mark.asyncio
async def test_worker_task_processor_times_out_expired_task_without_execution(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        invocation.deadline_at = current_time() - timedelta(seconds=1)
        await session.commit()
        task = make_task(invocation)
        consumer = FakeInvocationTaskConsumer([task])
        runtime = FakeRuntimeExecutor()
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime,
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        attempts = list(await session.scalars(select(InvocationAttempt)))
        assert processed == 1
        assert consumer.acked_message_ids == [task.stream_message_id]
        assert runtime.tasks == []
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.TIMEOUT
        assert refreshed_invocation.error_type == "TimeoutError"
        assert attempts == []


async def create_invocation(session: AsyncSession, status: InvocationStatus) -> Invocation:
    user = User(
        id=OWNER_ID,
        email=f"dev-{OWNER_ID}@example.local",
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

    queued_at = current_time()
    invocation = Invocation(
        owner_id=OWNER_ID,
        function_version_id=version.id,
        status=status,
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


def current_time() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
