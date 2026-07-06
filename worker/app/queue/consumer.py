"""Redis Streams consumer-group polling and task parsing."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class InvocationTask:
    stream_message_id: str
    invocation_id: UUID
    function_version_id: UUID
    owner_id: UUID
    attempt_number: int
    queued_at: datetime
    deadline_at: datetime


def decode_redis_value(value: bytes | str | int) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def normalize_stream_fields(fields: dict[bytes | str, bytes | str | int]) -> dict[str, str]:
    return {decode_redis_value(key): decode_redis_value(value) for key, value in fields.items()}


def parse_invocation_task(
    message_id: bytes | str,
    fields: dict[bytes | str, bytes | str | int],
) -> InvocationTask:
    normalized = normalize_stream_fields(fields)
    return InvocationTask(
        stream_message_id=decode_redis_value(message_id),
        invocation_id=UUID(normalized["invocation_id"]),
        function_version_id=UUID(normalized["function_version_id"]),
        owner_id=UUID(normalized["owner_id"]),
        attempt_number=int(normalized["attempt_number"]),
        queued_at=datetime.fromisoformat(normalized["queued_at"]),
        deadline_at=datetime.fromisoformat(normalized["deadline_at"]),
    )


class RedisStreamConsumer:
    def __init__(
        self,
        redis: Redis,
        stream_name: str,
        consumer_group: str,
        consumer_name: str,
    ) -> None:
        self.redis = redis
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name

    async def ensure_consumer_group(self) -> None:
        try:
            await self.redis.xgroup_create(
                name=self.stream_name,
                groupname=self.consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read_new_tasks(
        self,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[InvocationTask]:
        response = await self.redis.xreadgroup(
            groupname=self.consumer_group,
            consumername=self.consumer_name,
            streams={self.stream_name: ">"},
            count=count,
            block=block_ms,
        )
        return parse_xreadgroup_response(response)

    async def acknowledge(self, task: InvocationTask) -> int:
        return await self.redis.xack(
            self.stream_name,
            self.consumer_group,
            task.stream_message_id,
        )


def parse_xreadgroup_response(response: object) -> list[InvocationTask]:
    tasks: list[InvocationTask] = []
    for _stream_name, messages in response or []:
        for message_id, fields in messages:
            tasks.append(parse_invocation_task(message_id, fields))
    return tasks
