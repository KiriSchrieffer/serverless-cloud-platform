"""Redis Streams producer for accepted invocations."""

from typing import Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.app.models.invocation import Invocation

RedisField = bytes | bytearray | memoryview | str | int | float


class InvocationQueuePublishError(Exception):
    def __init__(self, invocation_id: str) -> None:
        super().__init__(f"Failed to publish invocation task: {invocation_id}")
        self.invocation_id = invocation_id


class InvocationQueuePublisherProtocol(Protocol):
    async def publish_invocation(
        self,
        invocation: Invocation,
        *,
        attempt_number: int = 1,
    ) -> str:
        """Publish an accepted invocation task and return the stream message id."""


def build_invocation_message_fields(
    invocation: Invocation,
    *,
    attempt_number: int = 1,
) -> dict[str, str]:
    return {
        "invocation_id": str(invocation.id),
        "function_version_id": str(invocation.function_version_id),
        "owner_id": str(invocation.owner_id),
        "attempt_number": str(attempt_number),
        "queued_at": invocation.queued_at.isoformat(),
        "deadline_at": invocation.deadline_at.isoformat(),
    }


class RedisInvocationQueuePublisher:
    def __init__(self, redis: Redis, stream_name: str) -> None:
        self.redis = redis
        self.stream_name = stream_name

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

        try:
            redis_fields = cast(
                dict[RedisField, RedisField],
                fields,
            )
            message_id = await self.redis.xadd(self.stream_name, redis_fields)
        except RedisError as exc:
            raise InvocationQueuePublishError(str(invocation.id)) from exc

        if isinstance(message_id, bytes):
            return message_id.decode("utf-8")
        return str(message_id)
