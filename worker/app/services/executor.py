"""Invocation execution orchestration."""

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from worker.app.queue.consumer import InvocationTask
from worker.app.runtime.docker_executor import RuntimeExecutionResult
from worker.app.services.invocation_state import InvocationCannotStartError, InvocationStateService

TaskLifecycleHook = Callable[[InvocationTask], Awaitable[None]]


class InvocationTaskConsumerProtocol(Protocol):
    async def read_new_tasks(self, count: int = 1, block_ms: int = 1000) -> list[InvocationTask]:
        """Read new invocation tasks from the queue."""

    async def acknowledge(self, task: InvocationTask) -> int:
        """Acknowledge a stream task after durable state changes."""


class RuntimeExecutorProtocol(Protocol):
    async def execute(self, task: InvocationTask) -> RuntimeExecutionResult:
        """Execute the invocation task and return the runtime result envelope."""


class WorkerTaskProcessor:
    def __init__(
        self,
        consumer: InvocationTaskConsumerProtocol,
        invocation_state: InvocationStateService,
        runtime_executor: RuntimeExecutorProtocol,
        worker_id: UUID | None = None,
        on_task_started: TaskLifecycleHook | None = None,
        on_task_finished: TaskLifecycleHook | None = None,
    ) -> None:
        self.consumer = consumer
        self.invocation_state = invocation_state
        self.runtime_executor = runtime_executor
        self.worker_id = worker_id
        self.on_task_started = on_task_started
        self.on_task_finished = on_task_finished

    async def process_once(self) -> int:
        tasks = await self.consumer.read_new_tasks(count=1, block_ms=1000)
        processed = 0
        for task in tasks:
            try:
                attempt = await self.invocation_state.mark_running(task, worker_id=self.worker_id)
            except InvocationCannotStartError as exc:
                if self.invocation_state.is_terminal_status(exc.status):
                    await self.consumer.acknowledge(task)
                    processed += 1
                    continue
                raise

            try:
                if self.on_task_started is not None:
                    await self.on_task_started(task)
                execution_result = await self.runtime_executor.execute(task)
            except Exception as exc:
                execution_result = RuntimeExecutionResult.failed(
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    exit_code=None,
                )
            finally:
                if self.on_task_finished is not None:
                    await self.on_task_finished(task)

            await self.invocation_state.mark_terminal(
                invocation_id=task.invocation_id,
                attempt_id=attempt.id,
                execution_result=execution_result,
            )
            await self.consumer.acknowledge(task)
            processed += 1
        return processed
