"""Standalone transactional-outbox dispatcher process."""

import asyncio
import logging

from redis.asyncio import Redis

from backend.app.core.config import settings
from backend.app.db.session import AsyncSessionLocal, engine
from backend.app.services.dispatch_outbox import InvocationOutboxDispatcher
from backend.app.services.invocation_queue import RedisInvocationQueuePublisher

logger = logging.getLogger(__name__)


async def dispatch_once(redis: Redis) -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        result = await InvocationOutboxDispatcher(
            session,
            RedisInvocationQueuePublisher(redis, settings.invocation_stream),
        ).dispatch_pending(limit=settings.dispatch_batch_size)
        return result.published, result.failed


async def run_dispatcher() -> None:
    redis = Redis.from_url(settings.redis_url)
    try:
        while True:
            try:
                published, failed = await dispatch_once(redis)
                if published or failed:
                    logger.info(
                        "Invocation dispatch batch published=%s failed=%s",
                        published,
                        failed,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Invocation dispatch batch failed")
            await asyncio.sleep(settings.dispatch_poll_seconds)
    finally:
        await redis.aclose()
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_dispatcher())
    except KeyboardInterrupt:
        logger.info("Invocation dispatcher stopped")


if __name__ == "__main__":
    main()
