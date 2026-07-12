"""Publish committed invocation outbox records to Redis Streams."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.dispatch import InvocationDispatch
from backend.app.models.invocation import Invocation
from backend.app.services.invocation_queue import (
    InvocationQueuePublishError,
    InvocationQueuePublisherProtocol,
)


@dataclass(frozen=True)
class DispatchBatchResult:
    published: int
    failed: int


class InvocationOutboxDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        publisher: InvocationQueuePublisherProtocol,
    ) -> None:
        self.session = session
        self.publisher = publisher

    async def dispatch_pending(self, *, limit: int) -> DispatchBatchResult:
        now = self.utcnow()
        result = await self.session.scalars(
            select(InvocationDispatch)
            .where(
                InvocationDispatch.published_at.is_(None),
                InvocationDispatch.available_at <= now,
            )
            .order_by(InvocationDispatch.created_at, InvocationDispatch.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        dispatches = list(result)
        published = 0
        failed = 0

        for dispatch in dispatches:
            invocation = await self.session.get(Invocation, dispatch.invocation_id)
            if invocation is None:
                dispatch.publish_attempts += 1
                dispatch.last_error = "Invocation record no longer exists"
                failed += 1
                continue

            dispatch.publish_attempts += 1
            try:
                message_id = await self.publisher.publish_invocation(
                    invocation,
                    attempt_number=dispatch.attempt_number,
                )
            except InvocationQueuePublishError as exc:
                dispatch.last_error = str(exc)
                failed += 1
            else:
                dispatch.published_message_id = message_id
                dispatch.published_at = self.utcnow()
                dispatch.last_error = None
                published += 1

        await self.session.commit()
        return DispatchBatchResult(published=published, failed=failed)

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
