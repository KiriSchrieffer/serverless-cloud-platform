"""Authentication, authorization, and invocation rate-limit API tests."""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.api.dependencies import get_db_session, get_rate_limiter
from backend.app.core.security import create_access_token, verify_password
from backend.app.main import create_app
from backend.app.models.invocation import Invocation
from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.user import User
from backend.app.services.rate_limiter import RateLimitDecision, RateLimiterUnavailableError

PASSWORD = "correct horse battery staple"
VERSION_PAYLOAD = {
    "runtime": "python3.11",
    "handler": "main.handler",
    "package_uri": "storage/packages/hello/v1/function.zip",
    "package_hash": "0123456789abcdef0123456789abcdef",
    "memory_limit_mb": 256,
    "cpu_limit": 0.5,
    "timeout_seconds": 30,
}


class ConfigurableRateLimiter:
    def __init__(self) -> None:
        self.allowed = True
        self.available = True
        self.keys: list[str] = []

    async def consume(self, key: str, *, cost: int = 1) -> RateLimitDecision:
        self.keys.append(key)
        if not self.available:
            raise RateLimiterUnavailableError("redis unavailable")
        return RateLimitDecision(
            allowed=self.allowed,
            remaining=99 if self.allowed else 0,
            retry_after_seconds=7,
        )


@pytest.fixture()
async def auth_api_client(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[AsyncClient, ConfigurableRateLimiter]]:
    from backend.app.core.config import settings

    monkeypatch.setattr(settings, "password_bcrypt_rounds", 4)
    app = create_app()
    limiter = ConfigurableRateLimiter()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_rate_limiter] = lambda: limiter

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client, limiter


async def register_and_login(client: AsyncClient, email: str) -> tuple[str, UUID]:
    register_response = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert register_response.status_code == 201
    user_id = UUID(register_response.json()["id"])
    login_response = await client.post(
        "/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert login_response.status_code == 200
    return login_response.json()["access_token"], user_id


@pytest.mark.asyncio
async def test_register_hashes_password_and_normalizes_email(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    client, _ = auth_api_client
    response = await client.post(
        "/auth/register",
        json={"email": "  User@Example.COM ", "password": PASSWORD},
    )

    assert response.status_code == 201
    assert response.json()["email"] == "user@example.com"
    assert "password" not in response.json()
    async with test_sessionmaker() as session:
        user = await session.scalar(select(User).where(User.email == "user@example.com"))
    assert user is not None
    assert user.password_hash != PASSWORD
    assert verify_password(PASSWORD, user.password_hash)


@pytest.mark.asyncio
async def test_register_rejects_duplicate_normalized_email(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    first = await client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": PASSWORD},
    )
    duplicate = await client.post(
        "/auth/register",
        json={"email": "USER@example.com", "password": PASSWORD},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Email is already registered"


@pytest.mark.asyncio
async def test_login_returns_bearer_token_that_authorizes_api_calls(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    token, user_id = await register_and_login(client, "user@example.com")

    response = await client.post(
        "/functions",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Owner-Id": str(uuid4()),
        },
        json={"name": "hello"},
    )

    assert response.status_code == 201
    assert response.json()["owner_id"] == str(user_id)


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_without_revealing_email_existence(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    await client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": PASSWORD},
    )

    wrong_password = await client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "wrong password"},
    )
    missing_user = await client.post(
        "/auth/login",
        json={"email": "missing@example.com", "password": "wrong"},
    )

    assert wrong_password.status_code == 401
    assert missing_user.status_code == 401
    assert wrong_password.json() == missing_user.json()


@pytest.mark.asyncio
async def test_protected_route_rejects_missing_invalid_and_expired_tokens(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    missing = await client.get("/functions")
    missing_workers = await client.get("/workers")
    invalid = await client.get(
        "/functions",
        headers={"Authorization": "Bearer not-a-token"},
    )
    expired_token = create_access_token(uuid4(), expires_delta=timedelta(seconds=-1))
    expired = await client.get(
        "/functions",
        headers={"Authorization": f"Bearer {expired_token}"},
    )

    assert missing.status_code == 401
    assert missing_workers.status_code == 401
    assert invalid.status_code == 401
    assert expired.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_valid_token_for_unavailable_user_returns_403(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    token = create_access_token(uuid4())

    response = await client.get(
        "/functions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "User account is unavailable"


@pytest.mark.asyncio
async def test_cross_user_function_and_invocation_access_is_rejected(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    first_token, _ = await register_and_login(client, "first@example.com")
    second_token, _ = await register_and_login(client, "second@example.com")
    first_headers = {"Authorization": f"Bearer {first_token}"}
    second_headers = {"Authorization": f"Bearer {second_token}"}

    await client.post("/functions", headers=first_headers, json={"name": "hello"})
    await client.post(
        "/functions/hello/versions",
        headers=first_headers,
        json=VERSION_PAYLOAD,
    )
    invoke = await client.post(
        "/functions/hello/invoke",
        headers=first_headers,
        json={"payload": {"name": "Ada"}},
    )
    invocation_id = invoke.json()["invocation_id"]

    function_access = await client.get("/functions/hello/versions", headers=second_headers)
    invocation_access = await client.get(
        f"/invocations/{invocation_id}",
        headers=second_headers,
    )

    assert function_access.status_code == 404
    assert invocation_access.status_code == 404


@pytest.mark.asyncio
async def test_rate_limited_invocation_returns_429_before_database_insert(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
    test_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    client, limiter = auth_api_client
    token, user_id = await register_and_login(client, "user@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/functions", headers=headers, json={"name": "hello"})
    await client.post(
        "/functions/hello/versions",
        headers=headers,
        json=VERSION_PAYLOAD,
    )
    limiter.allowed = False

    response = await client.post(
        "/functions/hello/invoke",
        headers=headers,
        json={"payload": {"name": "Ada"}},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert limiter.keys == [f"rate-limit:invocations:user:{user_id}"]
    async with test_sessionmaker() as session:
        invocation_count = await session.scalar(select(func.count(Invocation.id)))
        dispatch_count = await session.scalar(select(func.count(InvocationDispatch.id)))
    assert invocation_count == 0
    assert dispatch_count == 0


@pytest.mark.asyncio
async def test_invocation_fails_closed_when_rate_limiter_is_unavailable(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, limiter = auth_api_client
    token, _ = await register_and_login(client, "user@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    limiter.available = False

    response = await client.post(
        "/functions/hello/invoke",
        headers=headers,
        json={"payload": {}},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Invocation rate limiter is unavailable"


@pytest.mark.asyncio
async def test_password_over_72_utf8_bytes_is_rejected(
    auth_api_client: tuple[AsyncClient, ConfigurableRateLimiter],
) -> None:
    client, _ = auth_api_client
    response = await client.post(
        "/auth/register",
        json={"email": "user@example.com", "password": "密" * 25},
    )

    assert response.status_code == 422
