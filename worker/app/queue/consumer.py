"""Redis Streams consumer-group polling and task parsing."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
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
    recovered: bool = False


@dataclass(frozen=True)
class ClaimedInvocationTasks:
    next_start_id: str
    tasks: list[InvocationTask]


def decode_redis_value(value: bytes | str | int) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def normalize_stream_fields(fields: dict[bytes | str, bytes | str | int]) -> dict[str, str]:
    return {decode_redis_value(key): decode_redis_value(value) for key, value in fields.items()}


def parse_invocation_task(
    message_id: bytes | str,
    fields: dict[bytes | str, bytes | str | int],
    *,
    recovered: bool = False,
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
        recovered=recovered,
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

    async def claim_stale_tasks(
        self,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
        count: int = 10,
    ) -> ClaimedInvocationTasks:
        response = await self.redis.xautoclaim(
            name=self.stream_name,
            groupname=self.consumer_group,
            consumername=self.consumer_name,
            min_idle_time=min_idle_ms,
            start_id=start_id,
            count=count,
        )
        return parse_xautoclaim_response(response)

    async def claim_tasks_from_consumers(
        self,
        *,
        consumer_names: list[str],
        min_idle_ms: int,
        count: int,
    ) -> list[InvocationTask]:
        tasks: list[InvocationTask] = []
        for stale_consumer_name in consumer_names:
            remaining = count - len(tasks)
            if remaining <= 0:
                break
            pending = await self.redis.xpending_range(
                name=self.stream_name,
                groupname=self.consumer_group,
                min="-",
                max="+",
                count=remaining,
                consumername=stale_consumer_name,
                idle=min_idle_ms,
            )
            message_ids = [pending_message_id(item) for item in pending]
            if not message_ids:
                continue
            claim_message_ids = cast(
                list[int | bytes | str | memoryview],
                message_ids,
            )
            claimed = await self.redis.xclaim(
                name=self.stream_name,
                groupname=self.consumer_group,
                consumername=self.consumer_name,
                min_idle_time=min_idle_ms,
                message_ids=claim_message_ids,
            )
            claimed_messages = cast(
                list[tuple[bytes | str, dict[bytes | str, bytes | str | int]]],
                claimed,
            )
            tasks.extend(
                parse_invocation_task(message_id, fields, recovered=True)
                for message_id, fields in claimed_messages
            )
        return tasks


def pending_message_id(item: object) -> bytes | str:
    if isinstance(item, dict):
        message_id = item.get("message_id") or item.get(b"message_id")
        if isinstance(message_id, (bytes, str)):
            return message_id
    if isinstance(item, (list, tuple)) and item:
        message_id = item[0]
        if isinstance(message_id, (bytes, str)):
            return message_id
    raise ValueError("Redis XPENDING response is missing message_id")


def parse_xreadgroup_response(response: object) -> list[InvocationTask]:
    tasks: list[InvocationTask] = []
    streams = cast(
        list[
            tuple[
                bytes | str,
                list[tuple[bytes | str, dict[bytes | str, bytes | str | int]]],
            ]
        ],
        response or [],
    )
    for _stream_name, messages in streams:
        for message_id, fields in messages:
            tasks.append(parse_invocation_task(message_id, fields))
    return tasks


def parse_xautoclaim_response(response: object) -> ClaimedInvocationTasks:
    if not response:
        return ClaimedInvocationTasks(next_start_id="0-0", tasks=[])

    parts = cast(list[Any] | tuple[Any, ...], response)
    next_start_id = cast(bytes | str | int, parts[0])
    messages = cast(
        list[tuple[bytes | str, dict[bytes | str, bytes | str | int]]],
        parts[1],
    )
    return ClaimedInvocationTasks(
        next_start_id=decode_redis_value(next_start_id),
        tasks=[
            parse_invocation_task(message_id, fields, recovered=True)
            for message_id, fields in messages
        ],
    )
