from __future__ import annotations

import logging

import uvicorn

from src.cache import get_redis
from src.config import Settings, get_settings

from .app import create_app
from .publisher import UpdatePublisher

logger = logging.getLogger(__name__)


async def run_receiver(settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    redis = get_redis(settings.redis_url)
    publisher = UpdatePublisher(
        rabbitmq_url=settings.rabbitmq_url,
        exchange=settings.updates_exchange,
        queue=settings.updates_queue,
        shard_count=settings.updates_shards,
    )
    await publisher.connect()

    app = create_app(settings, redis, publisher)

    config = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104
        port=settings.webhook_port,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)

    logger.info(
        "webhook-receiver listening on 0.0.0.0:%s, public URL=%s, queue=%s",
        settings.webhook_port,
        settings.webhook_full_url,
        settings.updates_queue,
    )

    try:
        await server.serve()
    finally:
        await publisher.close()
        await redis.aclose()


__all__ = ["run_receiver"]
