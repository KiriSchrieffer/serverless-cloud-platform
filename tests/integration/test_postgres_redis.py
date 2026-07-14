"""Integration checks backed by real PostgreSQL and Redis services."""

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import settings
from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation
from backend.app.models.user import User
from backend.app.services.invocations import InvocationService
from backend.app.services.rate_limiter import RedisTokenBucketRateLimiter

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION_TESTS") != "1",
        reason="set RUN_INTEGRATION_TESTS=1 with PostgreSQL and Redis running",
    ),
]


@pytest.mark.asyncio
async def test_postgres_concurrent_idempotency_creates_one_invocation_and_dispatch() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = uuid4()
    function_name = f"integration-{uuid4().hex}"
    idempotency_key = f"duplicate-{uuid4().hex}"

    try:
        async with session_factory() as session:
            user = User(
                id=owner_id,
                email=f"{owner_id}@integration.example",
                password_hash="integration-only",
            )
            function = Function(owner_id=owner_id, name=function_name)
            session.add_all([user, function])
            await session.flush()
            session.add(
                FunctionVersion(
                    function_id=function.id,
                    version_number=1,
                    runtime="python3.11",
                    handler="main.handler",
                    package_uri="storage/packages/integration/function.zip",
                    package_hash="0123456789abcdef0123456789abcdef",
                    memory_limit_mb=256,
                    cpu_limit=0.5,
                    timeout_seconds=30,
                )
            )
            await session.commit()

        async def create_once() -> tuple[str, bool]:
            async with session_factory() as session:
                result = await InvocationService(session).create_invocation(
                    owner_id=owner_id,
                    function_name=function_name,
                    payload={"source": "integration"},
                    idempotency_key=idempotency_key,
                )
                return str(result.invocation.id), result.created

        first, second = await asyncio.gather(create_once(), create_once())

        assert first[0] == second[0]
        assert sorted([first[1], second[1]]) == [False, True]
        async with session_factory() as session:
            invocation_count = await session.scalar(
                select(func.count(Invocation.id)).where(
                    Invocation.owner_id == owner_id,
                    Invocation.idempotency_key == idempotency_key,
                )
            )
            dispatch_count = await session.scalar(
                select(func.count(InvocationDispatch.id))
                .join(Invocation, InvocationDispatch.invocation_id == Invocation.id)
                .where(Invocation.owner_id == owner_id)
            )
        assert invocation_count == 1
        assert dispatch_count == 1
    finally:
        async with session_factory() as session:
            await session.execute(delete(User).where(User.id == owner_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_redis_token_bucket_is_atomic_under_concurrency() -> None:
    redis = Redis.from_url(settings.redis_url)
    key = f"integration:rate-limit:{uuid4()}"
    limiter = RedisTokenBucketRateLimiter(redis, capacity=10, period_seconds=60)
    try:
        decisions = await asyncio.gather(*(limiter.consume(key) for _ in range(25)))

        assert sum(decision.allowed for decision in decisions) == 10
        denied = [decision for decision in decisions if not decision.allowed]
        assert len(denied) == 15
        assert all(decision.retry_after_seconds > 0 for decision in denied)
    finally:
        await redis.delete(key)
        await redis.aclose()
