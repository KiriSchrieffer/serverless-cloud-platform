import logging

from worker.app.core.config import settings

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Worker scaffold ready for stream=%s group=%s",
        settings.invocation_stream,
        settings.invocation_consumer_group,
    )


if __name__ == "__main__":
    main()
