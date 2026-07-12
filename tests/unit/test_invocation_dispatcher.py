from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.dispatch import InvocationDispatch
from backend.app.services.dispatch_outbox import (
    DispatchBatchResult,
    InvocationOutboxDispatcher,
)

VERSION_PAYLOAD = {
    "runtime": "python3.11",
    "handler": "main.handler",
    "package_uri": "storage/packages/dispatch/v1/function.zip",
    "package_hash": "0123456789abcdef0123456789abcdef",
    "memory_limit_mb": 256,
    "cpu_limit": 0.5,
    "timeout_seconds": 30,
}


@pytest.mark.asyncio
async def test_dispatcher_publishes_committed_outbox_once(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    fake_invocation_queue_publisher,
) -> None:
    invocation_id = await create_pending_invocation(api_client)

    async with test_sessionmaker() as session:
        first_result = await InvocationOutboxDispatcher(
            session,
            fake_invocation_queue_publisher,
        ).dispatch_pending(limit=10)

    assert first_result == DispatchBatchResult(published=1, failed=0)
    assert len(fake_invocation_queue_publisher.messages) == 1
    assert fake_invocation_queue_publisher.messages[0]["invocation_id"] == str(invocation_id)

    async with test_sessionmaker() as session:
        dispatch = await get_dispatch(session, invocation_id)
        second_result = await InvocationOutboxDispatcher(
            session,
            fake_invocation_queue_publisher,
        ).dispatch_pending(limit=10)

    assert dispatch.publish_attempts == 1
    assert dispatch.published_message_id == "fake-1"
    assert dispatch.published_at is not None
    assert dispatch.last_error is None
    assert second_result == DispatchBatchResult(published=0, failed=0)
    assert len(fake_invocation_queue_publisher.messages) == 1


@pytest.mark.asyncio
async def test_dispatcher_keeps_failed_publish_pending_for_retry(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    fake_invocation_queue_publisher,
) -> None:
    invocation_id = await create_pending_invocation(api_client)
    fake_invocation_queue_publisher.fail_next = True

    async with test_sessionmaker() as session:
        failed_result = await InvocationOutboxDispatcher(
            session,
            fake_invocation_queue_publisher,
        ).dispatch_pending(limit=10)

    async with test_sessionmaker() as session:
        pending_dispatch = await get_dispatch(session, invocation_id)

    assert failed_result == DispatchBatchResult(published=0, failed=1)
    assert pending_dispatch.publish_attempts == 1
    assert pending_dispatch.published_at is None
    assert pending_dispatch.last_error is not None

    async with test_sessionmaker() as session:
        retry_result = await InvocationOutboxDispatcher(
            session,
            fake_invocation_queue_publisher,
        ).dispatch_pending(limit=10)

    async with test_sessionmaker() as session:
        published_dispatch = await get_dispatch(session, invocation_id)

    assert retry_result == DispatchBatchResult(published=1, failed=0)
    assert published_dispatch.publish_attempts == 2
    assert published_dispatch.published_message_id == "fake-2"
    assert published_dispatch.published_at is not None
    assert published_dispatch.last_error is None


@pytest.mark.asyncio
async def test_dispatcher_skips_outbox_until_available_at(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    fake_invocation_queue_publisher,
) -> None:
    invocation_id = await create_pending_invocation(api_client)
    async with test_sessionmaker() as session:
        dispatch = await get_dispatch(session, invocation_id)
        dispatch.available_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)
        await session.commit()

    async with test_sessionmaker() as session:
        result = await InvocationOutboxDispatcher(
            session,
            fake_invocation_queue_publisher,
        ).dispatch_pending(limit=10)

    assert result == DispatchBatchResult(published=0, failed=0)
    assert fake_invocation_queue_publisher.messages == []


async def create_pending_invocation(api_client: AsyncClient) -> UUID:
    function_response = await api_client.post("/functions", json={"name": "dispatch"})
    assert function_response.status_code == 201
    version_response = await api_client.post(
        "/functions/dispatch/versions",
        json=VERSION_PAYLOAD,
    )
    assert version_response.status_code == 201
    invocation_response = await api_client.post(
        "/functions/dispatch/invoke",
        json={"payload": {"name": "Ada"}},
    )
    assert invocation_response.status_code == 202
    return UUID(invocation_response.json()["invocation_id"])


async def get_dispatch(session: AsyncSession, invocation_id: UUID) -> InvocationDispatch:
    dispatch = await session.scalar(
        select(InvocationDispatch).where(InvocationDispatch.invocation_id == invocation_id)
    )
    assert dispatch is not None
    return dispatch
