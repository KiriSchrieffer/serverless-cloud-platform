"""Invocation execution orchestration."""

from typing import Protocol
from uuid import UUID

from worker.app.queue.consumer import InvocationTask
from worker.app.services.invocation_state import InvocationStateService


class InvocationTaskConsumerProtocol(Protocol):
    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000) -> list[InvocationTask]:
        """Read new invocation tasks from the queue."""


class WorkerTaskProcessor:
    def __init__(
        self,
        consumer: InvocationTaskConsumerProtocol,
        invocation_state: InvocationStateService,
        worker_id: UUID | None = None,
    ) -> None:
        self.consumer = consumer
        self.invocation_state = invocation_state
        self.worker_id = worker_id

    async def process_once(self) -> int:
        tasks = await self.consumer.read_new_tasks(count=1, block_ms=1000)
        processed = 0
        for task in tasks:
            await self.invocation_state.mark_running(task, worker_id=self.worker_id)
            processed += 1
        return processed
