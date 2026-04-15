"""Entry point for ``MODE=receiver``.

Stands up the FastAPI app behind a uvicorn server, with Redis and the
RabbitMQ publisher wired in. Graceful shutdown: closes the publisher
connection and the Redis client on exit.
"""

from __future__ import annotations

import logging

import uvicorn

from src.cache.redis_client import get_redis
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
    )
    await publisher.connect()

    app = create_app(settings, redis, publisher)

    # log_config=None → defer to the root logger configured in main.py.
    config = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104 — bind inside a container; Ingress fronts it
        port=settings.webhook_port,
        log_config=None,
        access_log=False,  # noisy; re-enable via Prometheus in Phase 5
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
