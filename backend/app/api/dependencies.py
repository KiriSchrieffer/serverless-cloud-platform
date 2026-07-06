from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.session import AsyncSessionLocal
from backend.app.services.function_registry import FunctionRegistryService
from backend.app.services.invocation_queue import RedisInvocationQueuePublisher
from backend.app.services.invocations import InvocationService
from backend.app.services.storage import LocalLogStorageService, LocalPackageStorageService

DEVELOPMENT_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_current_user_id(
    x_owner_id: Annotated[str | None, Header(alias="X-Owner-Id")] = None,
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


def get_function_registry_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FunctionRegistryService:
    return FunctionRegistryService(session)


def get_invocation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvocationService:
    return InvocationService(session)


def get_package_storage_service() -> LocalPackageStorageService:
    return LocalPackageStorageService()


def get_log_storage_service() -> LocalLogStorageService:
    return LocalLogStorageService()


async def get_redis_client() -> AsyncIterator[Redis]:
    redis = Redis.from_url(settings.redis_url)
    try:
        yield redis
    finally:
        await redis.aclose()


def get_invocation_queue_publisher(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> RedisInvocationQueuePublisher:
    return RedisInvocationQueuePublisher(redis=redis, stream_name=settings.invocation_stream)
