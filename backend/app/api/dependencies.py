from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.core.security import InvalidAccessTokenError, decode_access_token
from backend.app.db.session import AsyncSessionLocal
from backend.app.models.user import User
from backend.app.services.auth import AuthService
from backend.app.services.function_registry import FunctionRegistryService
from backend.app.services.invocation_queue import RedisInvocationQueuePublisher
from backend.app.services.invocations import InvocationService
from backend.app.services.metrics import PlatformMetricsService
from backend.app.services.rate_limiter import (
    RateLimiterUnavailableError,
    RedisTokenBucketRateLimiter,
)
from backend.app.services.storage import LocalLogStorageService, LocalPackageStorageService

bearer_scheme = HTTPBearer(auto_error=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_current_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer access token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidAccessTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if await session.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is unavailable",
        )
    return user_id


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthService:
    return AuthService(session, bcrypt_rounds=settings.password_bcrypt_rounds)


def get_function_registry_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FunctionRegistryService:
    return FunctionRegistryService(session)


def get_invocation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvocationService:
    return InvocationService(session)


def get_metrics_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PlatformMetricsService:
    return PlatformMetricsService(session)


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


def get_rate_limiter(
    redis: Annotated[Redis, Depends(get_redis_client)],
) -> RedisTokenBucketRateLimiter:
    return RedisTokenBucketRateLimiter(
        redis,
        capacity=settings.invocation_rate_limit_capacity,
        period_seconds=settings.invocation_rate_limit_period_seconds,
    )


async def enforce_invocation_rate_limit(
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    limiter: Annotated[RedisTokenBucketRateLimiter, Depends(get_rate_limiter)],
) -> None:
    try:
        decision = await limiter.consume(f"rate-limit:invocations:user:{owner_id}")
    except RateLimiterUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Invocation rate limiter is unavailable",
        ) from exc
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Invocation rate limit exceeded",
            headers={"Retry-After": str(max(1, decision.retry_after_seconds))},
        )
