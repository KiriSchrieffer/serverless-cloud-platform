from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationStatus
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation
from backend.app.models.user import User
from worker.app.queue.consumer import parse_xreadgroup_response
from worker.app.services.executor import WorkerTaskProcessor
from worker.app.services.invocation_state import (
    InvocationCannotStartError,
    InvocationStateService,
)


OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeInvocationTaskConsumer:
    def __init__(self, tasks) -> None:
        self.tasks = tasks

    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000):
        return self.tasks[:count]


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
async def test_worker_task_processor_reads_task_and_marks_invocation_running(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, status=InvocationStatus.QUEUED)
        task = make_task(invocation)
        processor = WorkerTaskProcessor(
            consumer=FakeInvocationTaskConsumer([task]),
            invocation_state=InvocationStateService(session),
        )

        processed = await processor.process_once()

        refreshed_invocation = await session.get(Invocation, invocation.id)
        assert processed == 1
        assert refreshed_invocation is not None
        assert refreshed_invocation.status == InvocationStatus.RUNNING


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

    queued_at = datetime(2026, 6, 20, 10, 0, 0)
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
