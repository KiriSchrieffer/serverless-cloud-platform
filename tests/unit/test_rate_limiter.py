"""Redis token-bucket service tests."""

import pytest
from redis.exceptions import ConnectionError

from backend.app.services.rate_limiter import (
    TOKEN_BUCKET_SCRIPT,
    RateLimiterUnavailableError,
    RedisTokenBucketRateLimiter,
)


class FakeRedis:
    def __init__(self, result: list[int] | None = None) -> None:
        self.result = result or [1, 99, 0]
        self.calls: list[tuple[object, ...]] = []

    async def eval(self, *args: object) -> list[int]:
        self.calls.append(args)
        return self.result


class UnavailableRedis:
    async def eval(self, *args: object) -> list[int]:
        raise ConnectionError("redis unavailable")


@pytest.mark.asyncio
async def test_token_bucket_uses_atomic_server_time_lua_script() -> None:
    redis = FakeRedis()
    limiter = RedisTokenBucketRateLimiter(redis, capacity=100, period_seconds=60)

    decision = await limiter.consume("rate-limit:user:123")

    assert decision.allowed is True
    assert decision.remaining == 99
    assert decision.retry_after_seconds == 0
    assert redis.calls == [(TOKEN_BUCKET_SCRIPT, 1, "rate-limit:user:123", 100, 60000, 1)]
    assert "redis.call('TIME')" in TOKEN_BUCKET_SCRIPT
    assert "redis.call('HSET'" in TOKEN_BUCKET_SCRIPT
    assert "redis.call('PEXPIRE'" in TOKEN_BUCKET_SCRIPT


@pytest.mark.asyncio
async def test_token_bucket_rounds_retry_after_up_to_seconds() -> None:
    limiter = RedisTokenBucketRateLimiter(FakeRedis([0, 0, 1001]), capacity=2, period_seconds=3)

    decision = await limiter.consume("rate-limit:user:123")

    assert decision.allowed is False
    assert decision.retry_after_seconds == 2


@pytest.mark.asyncio
async def test_token_bucket_fails_closed_when_redis_is_unavailable() -> None:
    limiter = RedisTokenBucketRateLimiter(UnavailableRedis(), capacity=100, period_seconds=60)

    with pytest.raises(RateLimiterUnavailableError):
        await limiter.consume("rate-limit:user:123")


@pytest.mark.asyncio
async def test_token_bucket_rejects_invalid_configuration_and_cost() -> None:
    with pytest.raises(ValueError):
        RedisTokenBucketRateLimiter(FakeRedis(), capacity=0, period_seconds=60)

    limiter = RedisTokenBucketRateLimiter(FakeRedis(), capacity=1, period_seconds=60)
    with pytest.raises(ValueError):
        await limiter.consume("rate-limit:user:123", cost=2)
