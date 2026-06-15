import pytest
from httpx import AsyncClient

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
async def test_invoke_function_reuses_idempotency_key(api_client: AsyncClient) -> None:
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
