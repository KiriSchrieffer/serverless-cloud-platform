import asyncio
import logging
import socket
from collections.abc import Callable
from uuid import UUID, uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.worker import Worker
from worker.app.core.config import settings
from worker.app.db.session import AsyncSessionLocal, engine
from worker.app.queue.consumer import ClaimedInvocationTasks, RedisStreamConsumer
from worker.app.runtime.docker_executor import DockerRuntimeExecutor
from worker.app.services.executor import (
    InvocationTaskConsumerProtocol,
    RuntimeExecutorProtocol,
    WorkerTaskProcessor,
)
from worker.app.services.heartbeat import (
    WorkerHeartbeatService,
    WorkerRuntimeState,
)
from worker.app.services.invocation_state import InvocationStateService
from worker.app.services.recovery import StaleWorkerRecoveryService
from worker.app.services.retry import RetryPolicy

logger = logging.getLogger(__name__)

RuntimeExecutorFactory = Callable[[AsyncSession], RuntimeExecutorProtocol]


def build_consumer_name() -> str:
    return f"{socket.gethostname()}-{uuid4().hex[:8]}"


def default_runtime_executor_factory(session: AsyncSession) -> DockerRuntimeExecutor:
    return DockerRuntimeExecutor(session)


async def process_worker_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    consumer: InvocationTaskConsumerProtocol,
    *,
    runtime_executor_factory: RuntimeExecutorFactory = default_runtime_executor_factory,
    worker_id: UUID | None = None,
    runtime_state: WorkerRuntimeState | None = None,
    stale_worker_seconds: int = settings.stale_worker_seconds,
    max_attempts: int = settings.default_max_attempts,
    pending_message_claim_count: int = settings.pending_message_claim_count,
) -> int:
    async def on_task_started(_task) -> None:
        if runtime_state is not None:
            runtime_state.mark_task_started()

    async def on_task_finished(_task) -> None:
        if runtime_state is not None:
            runtime_state.mark_task_finished()

    async with sessionmaker() as session:
        await StaleWorkerRecoveryService(session).recover_stale_workers(
            stale_after_seconds=stale_worker_seconds,
            max_attempts=max_attempts,
        )
        processor = WorkerTaskProcessor(
            consumer=consumer,
            invocation_state=InvocationStateService(session),
            runtime_executor=runtime_executor_factory(session),
            worker_id=worker_id,
            on_task_started=on_task_started if runtime_state is not None else None,
            on_task_finished=on_task_finished if runtime_state is not None else None,
            retry_policy=RetryPolicy(max_attempts=max_attempts),
        )
        claimed_tasks = await claim_pending_tasks(
            consumer,
            min_idle_ms=stale_worker_seconds * 1000,
            count=pending_message_claim_count,
        )
        if claimed_tasks.tasks:
            return await processor.process_tasks(claimed_tasks.tasks)
        return await processor.process_once()


async def run_worker_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    consumer: InvocationTaskConsumerProtocol,
    *,
    runtime_executor_factory: RuntimeExecutorFactory = default_runtime_executor_factory,
    worker_id: UUID | None = None,
    runtime_state: WorkerRuntimeState | None = None,
    stale_worker_seconds: int = settings.stale_worker_seconds,
    max_attempts: int = settings.default_max_attempts,
    pending_message_claim_count: int = settings.pending_message_claim_count,
    max_iterations: int | None = None,
    retry_sleep_seconds: float = 1.0,
) -> int:
    processed_total = 0
    iterations = 0

    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            processed = await process_worker_once(
                sessionmaker,
                consumer,
                runtime_executor_factory=runtime_executor_factory,
                worker_id=worker_id,
                runtime_state=runtime_state,
                stale_worker_seconds=stale_worker_seconds,
                max_attempts=max_attempts,
                pending_message_claim_count=pending_message_claim_count,
            )
            processed_total += processed
            if processed:
                logger.info("Processed %s invocation task(s)", processed)
        except KeyboardInterrupt:
            raise
        except Exception:
            logger.exception("Worker loop iteration failed")
            await asyncio.sleep(retry_sleep_seconds)

    return processed_total


async def claim_pending_tasks(
    consumer: InvocationTaskConsumerProtocol,
    *,
    min_idle_ms: int,
    count: int,
) -> ClaimedInvocationTasks:
    claim_stale_tasks = getattr(consumer, "claim_stale_tasks", None)
    if claim_stale_tasks is None:
        return ClaimedInvocationTasks(next_start_id="0-0", tasks=[])
    return await claim_stale_tasks(min_idle_ms=min_idle_ms, count=count)


async def register_worker(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    hostname: str,
    max_concurrency: int,
) -> Worker:
    async with sessionmaker() as session:
        return await WorkerHeartbeatService(session).register_worker(
            hostname=hostname,
            max_concurrency=max_concurrency,
        )


async def record_worker_heartbeat(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    worker_id: UUID,
    runtime_state: WorkerRuntimeState,
) -> None:
    async with sessionmaker() as session:
        await WorkerHeartbeatService(session).record_heartbeat(
            worker_id,
            active_invocations=runtime_state.active_invocations,
            status=runtime_state.status,
        )


async def run_heartbeat_loop(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    worker_id: UUID,
    runtime_state: WorkerRuntimeState,
    stop_event: asyncio.Event,
    interval_seconds: float,
    max_iterations: int | None = None,
) -> int:
    iterations = 0
    while not stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
        await record_worker_heartbeat(
            sessionmaker,
            worker_id=worker_id,
            runtime_state=runtime_state,
        )
        iterations += 1

        if max_iterations is not None and iterations >= max_iterations:
            break

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue

    return iterations


async def mark_worker_offline(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    worker_id: UUID,
) -> None:
    async with sessionmaker() as session:
        await WorkerHeartbeatService(session).mark_offline(worker_id)


async def run_worker() -> None:
    redis = Redis.from_url(settings.redis_url)
    consumer_name = build_consumer_name()
    consumer = RedisStreamConsumer(
        redis=redis,
        stream_name=settings.invocation_stream,
        consumer_group=settings.invocation_consumer_group,
        consumer_name=consumer_name,
    )
    await consumer.ensure_consumer_group()
    worker = await register_worker(
        AsyncSessionLocal,
        hostname=socket.gethostname(),
        max_concurrency=settings.default_max_concurrency,
    )
    runtime_state = WorkerRuntimeState()
    stop_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        run_heartbeat_loop(
            AsyncSessionLocal,
            worker_id=worker.id,
            runtime_state=runtime_state,
            stop_event=stop_event,
            interval_seconds=settings.worker_heartbeat_seconds,
        )
    )
    logger.info(
        "Worker started id=%s stream=%s group=%s consumer=%s",
        worker.id,
        settings.invocation_stream,
        settings.invocation_consumer_group,
        consumer_name,
    )

    try:
        await run_worker_loop(
            AsyncSessionLocal,
            consumer,
            worker_id=worker.id,
            runtime_state=runtime_state,
        )
    finally:
        stop_event.set()
        await heartbeat_task
        await mark_worker_offline(AsyncSessionLocal, worker_id=worker.id)
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
