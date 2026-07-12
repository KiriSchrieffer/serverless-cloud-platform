from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationAttemptStatus
from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.invocation import Invocation, InvocationAttempt

VERSION_PAYLOAD = {
    "runtime": "python3.11",
    "handler": "main.handler",
    "package_uri": "storage/packages/hello/v1/function.zip",
    "package_hash": "0123456789abcdef0123456789abcdef",
    "memory_limit_mb": 256,
    "cpu_limit": 0.5,
    "timeout_seconds": 30,
}


async def create_function(api_client: AsyncClient, name: str = "hello") -> dict:
    response = await api_client.post("/functions", json={"name": name})
    assert response.status_code == 201
    return response.json()


async def create_function_version(
    api_client: AsyncClient,
    function_name: str = "hello",
    **overrides,
) -> dict:
    response = await api_client.post(
        f"/functions/{function_name}/versions",
        json={**VERSION_PAYLOAD, **overrides},
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_invoke_function_creates_queued_invocation_for_latest_version(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await create_function(api_client)
    await create_function_version(api_client)
    latest_version = await create_function_version(
        api_client,
        package_uri="storage/packages/hello/v2/function.zip",
        package_hash="abcdef0123456789abcdef0123456789",
    )

    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"name": "Ada"}},
    )

    assert invoke_response.status_code == 202
    accepted = invoke_response.json()
    assert accepted["status"] == "QUEUED"
    assert accepted["status_url"] == f"/invocations/{accepted['invocation_id']}"

    status_response = await api_client.get(accepted["status_url"])

    assert status_response.status_code == 200
    invocation = status_response.json()
    assert invocation["id"] == accepted["invocation_id"]
    assert invocation["status"] == "QUEUED"
    assert invocation["payload_inline"] == {"name": "Ada"}
    assert invocation["function_version_id"] == latest_version["id"]
    assert invocation["attempt_count"] == 0

    async with test_sessionmaker() as session:
        dispatches = list(await session.scalars(select(InvocationDispatch)))

    assert len(dispatches) == 1
    assert str(dispatches[0].invocation_id) == accepted["invocation_id"]
    assert dispatches[0].publish_attempts == 0
    assert dispatches[0].published_at is None


@pytest.mark.asyncio
async def test_invoke_function_can_target_specific_version(api_client: AsyncClient) -> None:
    await create_function(api_client)
    first_version = await create_function_version(api_client)
    await create_function_version(
        api_client,
        package_uri="storage/packages/hello/v2/function.zip",
        package_hash="abcdef0123456789abcdef0123456789",
    )

    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {}, "version_number": 1},
    )

    assert invoke_response.status_code == 202

    status_response = await api_client.get(invoke_response.json()["status_url"])

    assert status_response.status_code == 200
    assert status_response.json()["function_version_id"] == first_version["id"]


@pytest.mark.asyncio
async def test_invoke_function_reuses_idempotency_key(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await create_function(api_client)
    await create_function_version(api_client)

    first_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"request": 1}, "idempotency_key": "same-request"},
    )
    second_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"request": 2}, "idempotency_key": "same-request"},
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 202
    assert second_response.json()["invocation_id"] == first_response.json()["invocation_id"]

    status_response = await api_client.get(first_response.json()["status_url"])

    assert status_response.status_code == 200
    assert status_response.json()["payload_inline"] == {"request": 1}
    async with test_sessionmaker() as session:
        invocations = list(await session.scalars(select(Invocation)))
        dispatches = list(await session.scalars(select(InvocationDispatch)))

    assert len(invocations) == 1
    assert len(dispatches) == 1


@pytest.mark.asyncio
async def test_invoke_function_requires_existing_function_version(api_client: AsyncClient) -> None:
    await create_function(api_client)

    response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"name": "Ada"}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Function 'hello' has no versions"


@pytest.mark.asyncio
async def test_invoke_function_is_scoped_per_owner(api_client: AsyncClient) -> None:
    first_owner = "10000000-0000-0000-0000-000000000001"
    second_owner = "20000000-0000-0000-0000-000000000001"

    await api_client.post(
        "/functions",
        headers={"X-Owner-Id": first_owner},
        json={"name": "hello"},
    )
    await api_client.post(
        "/functions/hello/versions",
        headers={"X-Owner-Id": first_owner},
        json=VERSION_PAYLOAD,
    )

    response = await api_client.post(
        "/functions/hello/invoke",
        headers={"X-Owner-Id": second_owner},
        json={"payload": {"name": "Ada"}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Function 'hello' not found"


@pytest.mark.asyncio
async def test_get_invocation_is_scoped_per_owner(api_client: AsyncClient) -> None:
    first_owner = "10000000-0000-0000-0000-000000000001"
    second_owner = "20000000-0000-0000-0000-000000000001"

    await api_client.post(
        "/functions",
        headers={"X-Owner-Id": first_owner},
        json={"name": "hello"},
    )
    await api_client.post(
        "/functions/hello/versions",
        headers={"X-Owner-Id": first_owner},
        json=VERSION_PAYLOAD,
    )
    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        headers={"X-Owner-Id": first_owner},
        json={"payload": {"name": "Ada"}},
    )

    assert invoke_response.status_code == 202

    forbidden_response = await api_client.get(
        invoke_response.json()["status_url"],
        headers={"X-Owner-Id": second_owner},
    )

    assert forbidden_response.status_code == 404


@pytest.mark.asyncio
async def test_get_invocation_logs_returns_empty_text_when_no_logs(
    api_client: AsyncClient,
) -> None:
    await create_function(api_client)
    await create_function_version(api_client)
    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"name": "Ada"}},
    )

    logs_response = await api_client.get(f"{invoke_response.json()['status_url']}/logs")

    assert logs_response.status_code == 200
    assert logs_response.text == ""
    assert logs_response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_get_invocation_logs_returns_latest_attempt_logs(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    await create_function(api_client)
    await create_function_version(api_client)
    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"name": "Ada"}},
    )
    invocation_id = UUID(invoke_response.json()["invocation_id"])

    logs_dir = tmp_path / "storage" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "first.log").write_text("first attempt", encoding="utf-8")
    (logs_dir / "second.log").write_text("second attempt", encoding="utf-8")
    async with test_sessionmaker() as session:
        await create_attempt(
            session,
            invocation_id=invocation_id,
            attempt_number=1,
            logs_ref="storage/logs/first.log",
        )
        await create_attempt(
            session,
            invocation_id=invocation_id,
            attempt_number=2,
            logs_ref="storage/logs/second.log",
        )

    logs_response = await api_client.get(f"{invoke_response.json()['status_url']}/logs")

    assert logs_response.status_code == 200
    assert logs_response.text == "second attempt"


@pytest.mark.asyncio
async def test_get_invocation_logs_is_scoped_per_owner(api_client: AsyncClient) -> None:
    first_owner = "10000000-0000-0000-0000-000000000001"
    second_owner = "20000000-0000-0000-0000-000000000001"

    await api_client.post(
        "/functions",
        headers={"X-Owner-Id": first_owner},
        json={"name": "hello"},
    )
    await api_client.post(
        "/functions/hello/versions",
        headers={"X-Owner-Id": first_owner},
        json=VERSION_PAYLOAD,
    )
    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        headers={"X-Owner-Id": first_owner},
        json={"payload": {"name": "Ada"}},
    )

    forbidden_response = await api_client.get(
        f"{invoke_response.json()['status_url']}/logs",
        headers={"X-Owner-Id": second_owner},
    )

    assert forbidden_response.status_code == 404


@pytest.mark.asyncio
async def test_get_invocation_logs_returns_404_when_log_file_is_missing(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await create_function(api_client)
    await create_function_version(api_client)
    invoke_response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"name": "Ada"}},
    )
    invocation_id = UUID(invoke_response.json()["invocation_id"])
    async with test_sessionmaker() as session:
        await create_attempt(
            session,
            invocation_id=invocation_id,
            attempt_number=1,
            logs_ref="storage/logs/missing.log",
        )

    logs_response = await api_client.get(f"{invoke_response.json()['status_url']}/logs")

    assert logs_response.status_code == 404
    assert logs_response.json()["detail"] == "Invocation logs 'storage/logs/missing.log' not found"


@pytest.mark.asyncio
async def test_invoke_function_is_durably_accepted_before_queue_dispatch(
    api_client: AsyncClient,
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await create_function(api_client)
    await create_function_version(api_client)

    response = await api_client.post(
        "/functions/hello/invoke",
        json={"payload": {"name": "Ada"}},
    )

    assert response.status_code == 202
    invocation_id = UUID(response.json()["invocation_id"])
    async with test_sessionmaker() as session:
        invocation = await session.get(Invocation, invocation_id)
        dispatch = await session.scalar(
            select(InvocationDispatch).where(
                InvocationDispatch.invocation_id == invocation_id
            )
        )

    assert invocation is not None
    assert dispatch is not None
    assert dispatch.published_at is None


async def create_attempt(
    session: AsyncSession,
    *,
    invocation_id: UUID,
    attempt_number: int,
    logs_ref: str,
) -> None:
    timestamp = datetime(2026, 7, 6, 10, 0, 0)
    session.add(
        InvocationAttempt(
            invocation_id=invocation_id,
            attempt_number=attempt_number,
            status=InvocationAttemptStatus.FAILED,
            logs_ref=logs_ref,
            started_at=timestamp,
            completed_at=timestamp,
        )
    )
    await session.commit()
