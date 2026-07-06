import hashlib
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

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
async def test_upload_function_version_stores_package_and_hashes_contents(
    api_client: AsyncClient,
) -> None:
    await api_client.post("/functions", json={"name": "hello"})
    package_bytes = build_zip({"main.py": "def handler(event, context): return event\n"})

    response = await api_client.post(
        "/functions/hello/versions/upload",
        data={
            "runtime": "python3.11",
            "handler": "main.handler",
            "memory_limit_mb": "512",
            "cpu_limit": "1.0",
            "timeout_seconds": "45",
        },
        files={"package": ("function.zip", package_bytes, "application/zip")},
    )

    assert response.status_code == 201
    version = response.json()
    assert version["version_number"] == 1
    assert version["runtime"] == "python3.11"
    assert version["handler"] == "main.handler"
    assert version["memory_limit_mb"] == 512
    assert version["cpu_limit"] == 1.0
    assert version["timeout_seconds"] == 45
    assert version["package_hash"] == hashlib.sha256(package_bytes).hexdigest()
    assert version["package_uri"].endswith(
        "/00000000-0000-0000-0000-000000000001/hello/v1/function.zip"
    )


@pytest.mark.asyncio
async def test_upload_function_version_uses_next_version_number(
    api_client: AsyncClient,
) -> None:
    await api_client.post("/functions", json={"name": "hello"})
    first_package = build_zip({"main.py": "def handler(event, context): return event\n"})
    second_package = build_zip({"main.py": "def handler(event, context): return {'v': 2}\n"})

    first_response = await api_client.post(
        "/functions/hello/versions/upload",
        files={"package": ("function.zip", first_package, "application/zip")},
    )
    second_response = await api_client.post(
        "/functions/hello/versions/upload",
        files={"package": ("function.zip", second_package, "application/zip")},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["version_number"] == 1
    assert second_response.json()["version_number"] == 2
    assert second_response.json()["package_uri"].endswith(
        "/00000000-0000-0000-0000-000000000001/hello/v2/function.zip"
    )


@pytest.mark.asyncio
async def test_upload_function_version_rejects_missing_handler_module(
    api_client: AsyncClient,
) -> None:
    await api_client.post("/functions", json={"name": "hello"})
    package_bytes = build_zip({"other.py": "def handler(event, context): return event\n"})

    response = await api_client.post(
        "/functions/hello/versions/upload",
        files={"package": ("function.zip", package_bytes, "application/zip")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Package must contain handler module 'main.py'"


@pytest.mark.asyncio
async def test_upload_function_version_rejects_invalid_zip(api_client: AsyncClient) -> None:
    await api_client.post("/functions", json={"name": "hello"})

    response = await api_client.post(
        "/functions/hello/versions/upload",
        files={"package": ("function.zip", b"not a zip", "application/zip")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Package file must be a valid zip archive"


@pytest.mark.asyncio
async def test_upload_function_version_returns_404_for_missing_function(
    api_client: AsyncClient,
) -> None:
    package_bytes = build_zip({"main.py": "def handler(event, context): return event\n"})

    response = await api_client.post(
        "/functions/missing/versions/upload",
        files={"package": ("function.zip", package_bytes, "application/zip")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Function 'missing' not found"


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


def build_zip(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for path, contents in files.items():
            archive.writestr(path, contents)
    return buffer.getvalue()
