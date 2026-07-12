"""Shared pytest fixtures."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import Header, HTTPException, status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.api.dependencies import (
    enforce_invocation_rate_limit,
    get_current_user_id,
    get_db_session,
    get_log_storage_service,
    get_package_storage_service,
)
from backend.app.main import create_app
from backend.app.models import Base
from backend.app.models.invocation import Invocation
from backend.app.services.invocation_queue import (
    InvocationQueuePublishError,
    build_invocation_message_fields,
)
from backend.app.services.storage import LocalLogStorageService, LocalPackageStorageService

DEVELOPMENT_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeInvocationQueuePublisher:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.fail_next = False

    async def publish_invocation(
        self,
        invocation: Invocation,
        *,
        attempt_number: int = 1,
    ) -> str:
        fields = build_invocation_message_fields(
            invocation,
            attempt_number=attempt_number,
        )
        if self.fail_next:
            self.fail_next = False
            self.messages.append(fields)
            raise InvocationQueuePublishError(fields["invocation_id"])

        self.messages.append(fields)
        return f"fake-{len(self.messages)}"


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
def fake_invocation_queue_publisher() -> FakeInvocationQueuePublisher:
    return FakeInvocationQueuePublisher()


@pytest.fixture()
async def api_client(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with test_sessionmaker() as session:
            yield session

    async def override_current_user_id(
        x_owner_id: str | None = Header(default=None, alias="X-Owner-Id"),
    ) -> UUID:
        if x_owner_id is None:
            return DEVELOPMENT_OWNER_ID
        try:
            return UUID(x_owner_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Owner-Id must be a valid UUID",
            ) from exc

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user_id] = override_current_user_id
    app.dependency_overrides[enforce_invocation_rate_limit] = lambda: None
    app.dependency_overrides[get_package_storage_service] = lambda: LocalPackageStorageService(
        package_storage_dir=tmp_path / "packages",
        workspace_root=tmp_path,
    )
    app.dependency_overrides[get_log_storage_service] = lambda: LocalLogStorageService(
        workspace_root=tmp_path,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
