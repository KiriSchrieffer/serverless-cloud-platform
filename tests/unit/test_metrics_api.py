from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus, WorkerStatus
from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation, InvocationAttempt
from backend.app.models.user import User
from backend.app.models.worker import Worker

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_OWNER_ID = UUID("10000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_workers_endpoint_returns_worker_health(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = current_time()
    async with test_sessionmaker() as session:
        session.add_all(
            [
                make_worker(
                    hostname="fresh-worker",
                    status=WorkerStatus.RUNNING,
                    last_heartbeat=now,
                    active_invocations=2,
                ),
                make_worker(
                    hostname="stale-worker",
                    status=WorkerStatus.IDLE,
                    last_heartbeat=now - timedelta(seconds=60),
                    active_invocations=0,
                ),
                make_worker(
                    hostname="offline-worker",
                    status=WorkerStatus.OFFLINE,
                    last_heartbeat=now - timedelta(seconds=60),
                    active_invocations=0,
                ),
            ]
        )
        await session.commit()

    response = await api_client.get("/workers")

    assert response.status_code == 200
    workers = {worker["hostname"]: worker for worker in response.json()}
    assert workers["fresh-worker"]["status"] == "RUNNING"
    assert workers["fresh-worker"]["consumer_name"] == "fresh-worker-consumer"
    assert workers["fresh-worker"]["active_invocations"] == 2
    assert workers["fresh-worker"]["stale"] is False
    assert workers["fresh-worker"]["heartbeat_age_seconds"] >= 0
    assert workers["stale-worker"]["status"] == "IDLE"
    assert workers["stale-worker"]["stale"] is True
    assert workers["offline-worker"]["status"] == "OFFLINE"
    assert workers["offline-worker"]["stale"] is False


@pytest.mark.asyncio
async def test_metrics_summary_returns_empty_counts(api_client: AsyncClient) -> None:
    response = await api_client.get("/metrics/summary")

    assert response.status_code == 200
    assert response.json() == {
        "invocations": {
            "total": 0,
            "terminal": 0,
            "queued": 0,
            "running": 0,
            "retrying": 0,
            "succeeded": 0,
            "failed": 0,
            "timeout": 0,
            "canceled": 0,
            "success_rate": 0.0,
            "error_rate": 0.0,
            "retry_count": 0,
            "throughput_per_minute": 0.0,
            "average_latency_ms": None,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "p99_latency_ms": None,
            "average_execution_ms": None,
            "p95_execution_ms": None,
        },
        "queue": {
            "depth": 0,
            "oldest_age_seconds": None,
            "pending_dispatches": 0,
            "oldest_dispatch_age_seconds": None,
        },
        "workers": {
            "total": 0,
            "active": 0,
            "stale": 0,
            "offline": 0,
            "active_invocations": 0,
        },
    }


@pytest.mark.asyncio
async def test_metrics_summary_aggregates_invocations_and_workers(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = current_time()
    async with test_sessionmaker() as session:
        owner_version = await create_function_version(session, owner_id=OWNER_ID)
        other_version = await create_function_version(session, owner_id=OTHER_OWNER_ID)
        succeeded = create_invocation(
            owner_id=OWNER_ID,
            function_version_id=owner_version.id,
            status=InvocationStatus.SUCCEEDED,
            queued_at=now - timedelta(seconds=50),
        )
        failed = create_invocation(
            owner_id=OWNER_ID,
            function_version_id=owner_version.id,
            status=InvocationStatus.FAILED,
            queued_at=now - timedelta(seconds=40),
        )
        timed_out = create_invocation(
            owner_id=OWNER_ID,
            function_version_id=owner_version.id,
            status=InvocationStatus.TIMEOUT,
            queued_at=now - timedelta(seconds=30),
        )
        queued = create_invocation(
            owner_id=OWNER_ID,
            function_version_id=owner_version.id,
            status=InvocationStatus.QUEUED,
            queued_at=now - timedelta(seconds=20),
        )
        other_owner_invocation = create_invocation(
            owner_id=OTHER_OWNER_ID,
            function_version_id=other_version.id,
            status=InvocationStatus.SUCCEEDED,
            queued_at=now - timedelta(seconds=10),
        )
        session.add_all([succeeded, failed, timed_out, queued, other_owner_invocation])
        await session.flush()
        failed.attempt_count = 2
        session.add_all(
            [
                create_attempt(succeeded, InvocationAttemptStatus.SUCCEEDED, 100),
                create_attempt(
                    failed,
                    InvocationAttemptStatus.FAILED,
                    150,
                    attempt_number=1,
                ),
                create_attempt(
                    failed,
                    InvocationAttemptStatus.FAILED,
                    200,
                    attempt_number=2,
                ),
                create_attempt(timed_out, InvocationAttemptStatus.TIMEOUT, 400),
                create_attempt(other_owner_invocation, InvocationAttemptStatus.SUCCEEDED, 999),
                InvocationDispatch(
                    invocation_id=queued.id,
                    attempt_number=1,
                    created_at=queued.queued_at,
                    available_at=queued.queued_at,
                ),
                make_worker(
                    hostname="active-worker",
                    status=WorkerStatus.RUNNING,
                    last_heartbeat=now,
                    active_invocations=2,
                ),
                make_worker(
                    hostname="stale-worker",
                    status=WorkerStatus.IDLE,
                    last_heartbeat=now - timedelta(seconds=60),
                    active_invocations=0,
                ),
                make_worker(
                    hostname="offline-worker",
                    status=WorkerStatus.OFFLINE,
                    last_heartbeat=now - timedelta(seconds=60),
                    active_invocations=0,
                ),
            ]
        )
        await session.commit()

    response = await api_client.get("/metrics/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["invocations"] == {
        "total": 4,
        "terminal": 3,
        "queued": 1,
        "running": 0,
        "retrying": 0,
        "succeeded": 1,
        "failed": 1,
        "timeout": 1,
        "canceled": 0,
        "success_rate": 0.3333,
        "error_rate": 0.6667,
        "retry_count": 1,
        "throughput_per_minute": 3.0,
        "average_latency_ms": 1000.0,
        "p50_latency_ms": 1000.0,
        "p95_latency_ms": 1000.0,
        "p99_latency_ms": 1000.0,
        "average_execution_ms": 212.5,
        "p95_execution_ms": 400.0,
    }
    assert summary["queue"] == {
        "depth": 1,
        "oldest_age_seconds": 20,
        "pending_dispatches": 1,
        "oldest_dispatch_age_seconds": 20,
    }
    assert summary["workers"] == {
        "total": 3,
        "active": 1,
        "stale": 1,
        "offline": 1,
        "active_invocations": 2,
    }


async def create_function_version(session: AsyncSession, *, owner_id: UUID) -> FunctionVersion:
    user = User(
        id=owner_id,
        email=f"metrics-{owner_id}-{uuid4().hex}@example.local",
        password_hash="development-only",
    )
    function = Function(owner_id=owner_id, name=f"hello-{uuid4().hex}")
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
    return version


def create_invocation(
    *,
    owner_id: UUID,
    function_version_id: UUID,
    status: InvocationStatus,
    queued_at: datetime,
) -> Invocation:
    completed_at = queued_at + timedelta(seconds=1)
    return Invocation(
        owner_id=owner_id,
        function_version_id=function_version_id,
        status=status,
        payload_inline={},
        queued_at=queued_at,
        started_at=queued_at if status != InvocationStatus.QUEUED else None,
        completed_at=completed_at if status in terminal_statuses() else None,
        deadline_at=queued_at + timedelta(seconds=30),
        attempt_count=1 if status in terminal_statuses() else 0,
    )


def create_attempt(
    invocation: Invocation,
    status: InvocationAttemptStatus,
    duration_ms: int,
    attempt_number: int = 1,
) -> InvocationAttempt:
    started_at = datetime(2026, 7, 6, 10, 0, 0)
    return InvocationAttempt(
        invocation_id=invocation.id,
        attempt_number=attempt_number,
        status=status,
        started_at=started_at,
        completed_at=started_at + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
    )


def make_worker(
    *,
    hostname: str,
    status: WorkerStatus,
    last_heartbeat: datetime,
    active_invocations: int,
) -> Worker:
    return Worker(
        hostname=hostname,
        consumer_name=f"{hostname}-consumer",
        status=status,
        last_heartbeat=last_heartbeat,
        active_invocations=active_invocations,
        max_concurrency=2,
        started_at=last_heartbeat,
    )


def terminal_statuses() -> set[InvocationStatus]:
    return {
        InvocationStatus.SUCCEEDED,
        InvocationStatus.FAILED,
        InvocationStatus.TIMEOUT,
        InvocationStatus.CANCELED,
    }


def current_time() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
