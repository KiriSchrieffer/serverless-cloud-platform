"""Atomic Redis token-bucket rate limiting service."""

from dataclasses import dataclass
from math import ceil

from redis.asyncio import Redis
from redis.exceptions import RedisError

TOKEN_BUCKET_SCRIPT = """
local capacity = tonumber(ARGV[1])
local period_ms = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local redis_time = redis.call('TIME')
local now_ms = redis_time[1] * 1000 + math.floor(redis_time[2] / 1000)
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at_ms')
local tokens = tonumber(bucket[1]) or capacity
local updated_at_ms = tonumber(bucket[2]) or now_ms
local elapsed_ms = math.max(0, now_ms - updated_at_ms)

tokens = math.min(capacity, tokens + elapsed_ms * capacity / period_ms)

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
else
    retry_after_ms = math.ceil((cost - tokens) * period_ms / capacity)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_at_ms', now_ms)
redis.call('PEXPIRE', KEYS[1], math.ceil(period_ms * 2))
return {allowed, math.floor(tokens), retry_after_ms}
"""


class RateLimiterUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RedisTokenBucketRateLimiter:
    def __init__(self, redis: Redis, *, capacity: int, period_seconds: int) -> None:
        if capacity <= 0 or period_seconds <= 0:
            raise ValueError("Rate-limit capacity and period must be positive")
        self.redis = redis
        self.capacity = capacity
        self.period_ms = period_seconds * 1000

    async def consume(self, key: str, *, cost: int = 1) -> RateLimitDecision:
        if cost <= 0 or cost > self.capacity:
            raise ValueError("Rate-limit cost must be between 1 and capacity")
        try:
            result = await self.redis.eval(
                TOKEN_BUCKET_SCRIPT,
                1,
                key,
                self.capacity,
                self.period_ms,
                cost,
            )
        except RedisError as exc:
            raise RateLimiterUnavailableError("Redis rate limiter is unavailable") from exc

        allowed, remaining, retry_after_ms = (int(value) for value in result)
        return RateLimitDecision(
            allowed=bool(allowed),
            remaining=remaining,
            retry_after_seconds=ceil(retry_after_ms / 1000),
        )
