from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.api.dependencies import get_db_session
from backend.app.main import create_app
from backend.app.models import Base

VERSION_PAYLOAD = {
    "runtime": "python3.11",
    "handler": "main.handler",
    "package_uri": "storage/packages/hello/v1/function.zip",
    "package_hash": "0123456789abcdef0123456789abcdef",
    "memory_limit_mb": 256,
    "cpu_limit": 0.5,
    "timeout_seconds": 30,
}


@pytest.fixture()
async def test_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture()
async def api_client(
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_create_and_list_functions(api_client: AsyncClient) -> None:
    create_response = await api_client.post("/functions", json={"name": "hello"})

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "hello"
    assert created["owner_id"] == "00000000-0000-0000-0000-000000000001"

    list_response = await api_client.get("/functions")

    assert list_response.status_code == 200
    assert [item["name"] for item in list_response.json()] == ["hello"]


@pytest.mark.asyncio
async def test_create_function_rejects_duplicate_name_for_same_owner(
    api_client: AsyncClient,
) -> None:
    first_response = await api_client.post("/functions", json={"name": "hello"})
    duplicate_response = await api_client.post("/functions", json={"name": "hello"})

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"] == "Function 'hello' already exists"


@pytest.mark.asyncio
async def test_function_names_are_scoped_per_owner(api_client: AsyncClient) -> None:
    first_owner = "10000000-0000-0000-0000-000000000001"
    second_owner = "20000000-0000-0000-0000-000000000001"

    first_response = await api_client.post(
        "/functions",
        headers={"X-Owner-Id": first_owner},
        json={"name": "hello"},
    )
    second_response = await api_client.post(
        "/functions",
        headers={"X-Owner-Id": second_owner},
        json={"name": "hello"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    first_list = await api_client.get("/functions", headers={"X-Owner-Id": first_owner})
    second_list = await api_client.get("/functions", headers={"X-Owner-Id": second_owner})

    assert len(first_list.json()) == 1
    assert len(second_list.json()) == 1


@pytest.mark.asyncio
async def test_create_function_validates_name(api_client: AsyncClient) -> None:
    response = await api_client.post("/functions", json={"name": "123 invalid"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_and_list_function_versions(api_client: AsyncClient) -> None:
    await api_client.post("/functions", json={"name": "hello"})

    first_response = await api_client.post("/functions/hello/versions", json=VERSION_PAYLOAD)
    second_response = await api_client.post(
        "/functions/hello/versions",
        json={
            **VERSION_PAYLOAD,
            "package_uri": "storage/packages/hello/v2/function.zip",
            "package_hash": "abcdef0123456789abcdef0123456789",
            "memory_limit_mb": 512,
            "timeout_seconds": 45,
        },
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["version_number"] == 1
    assert second_response.json()["version_number"] == 2
    assert second_response.json()["memory_limit_mb"] == 512
    assert second_response.json()["timeout_seconds"] == 45

    list_response = await api_client.get("/functions/hello/versions")

    assert list_response.status_code == 200
    assert [item["version_number"] for item in list_response.json()] == [1, 2]


@pytest.mark.asyncio
async def test_function_versions_are_scoped_per_owner(api_client: AsyncClient) -> None:
    first_owner = "10000000-0000-0000-0000-000000000001"
    second_owner = "20000000-0000-0000-0000-000000000001"

    await api_client.post(
        "/functions",
        headers={"X-Owner-Id": first_owner},
        json={"name": "hello"},
    )
    first_version = await api_client.post(
        "/functions/hello/versions",
        headers={"X-Owner-Id": first_owner},
        json=VERSION_PAYLOAD,
    )
    second_owner_version = await api_client.post(
        "/functions/hello/versions",
        headers={"X-Owner-Id": second_owner},
        json=VERSION_PAYLOAD,
    )

    assert first_version.status_code == 201
    assert second_owner_version.status_code == 404
    assert second_owner_version.json()["detail"] == "Function 'hello' not found"


@pytest.mark.asyncio
async def test_list_function_versions_returns_404_for_missing_function(
    api_client: AsyncClient,
) -> None:
    response = await api_client.get("/functions/missing/versions")

    assert response.status_code == 404
    assert response.json()["detail"] == "Function 'missing' not found"


@pytest.mark.asyncio
async def test_create_function_version_validates_runtime_and_limits(
    api_client: AsyncClient,
) -> None:
    await api_client.post("/functions", json={"name": "hello"})

    response = await api_client.post(
        "/functions/hello/versions",
        json={
            **VERSION_PAYLOAD,
            "runtime": "nodejs20",
            "memory_limit_mb": 32,
            "timeout_seconds": 0,
        },
    )

    assert response.status_code == 422
