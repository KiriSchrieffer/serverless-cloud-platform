from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import WorkerStatus
from worker.app.services.heartbeat import (
    WorkerHeartbeatService,
    WorkerNotFoundError,
    WorkerRuntimeState,
)


def test_worker_runtime_state_tracks_active_invocations() -> None:
    state = WorkerRuntimeState()

    state.mark_task_started()
    state.mark_task_started()

    assert state.active_invocations == 2
    assert state.status == WorkerStatus.RUNNING

    state.mark_task_finished()
    state.mark_task_finished()
    state.mark_task_finished()

    assert state.active_invocations == 0
    assert state.status == WorkerStatus.IDLE


@pytest.mark.asyncio
async def test_worker_heartbeat_service_registers_and_updates_worker(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        service = WorkerHeartbeatService(session)
        worker = await service.register_worker(hostname="worker-host", max_concurrency=2)
        first_heartbeat = worker.last_heartbeat

        refreshed = await service.record_heartbeat(
            worker.id,
            active_invocations=1,
            status=WorkerStatus.RUNNING,
        )

        assert refreshed.hostname == "worker-host"
        assert refreshed.status == WorkerStatus.RUNNING
        assert refreshed.active_invocations == 1
        assert refreshed.max_concurrency == 2
        assert refreshed.last_heartbeat >= first_heartbeat


@pytest.mark.asyncio
async def test_worker_heartbeat_service_marks_worker_offline(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        service = WorkerHeartbeatService(session)
        worker = await service.register_worker(hostname="worker-host", max_concurrency=2)
        await service.record_heartbeat(
            worker.id,
            active_invocations=1,
            status=WorkerStatus.RUNNING,
        )

        offline = await service.mark_offline(worker.id)

        assert offline.status == WorkerStatus.OFFLINE
        assert offline.active_invocations == 0


@pytest.mark.asyncio
async def test_worker_heartbeat_service_rejects_missing_worker(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with test_sessionmaker() as session:
        with pytest.raises(WorkerNotFoundError):
            await WorkerHeartbeatService(session).record_heartbeat(
                uuid4(),
                active_invocations=0,
                status=WorkerStatus.IDLE,
            )
