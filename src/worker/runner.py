from __future__ import annotations

import asyncio
import logging
import signal

from src.config import Settings, get_settings

from .bot import Bot
from .consumer import UpdateConsumer

logger = logging.getLogger(__name__)


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    bot = Bot(
        settings.telegram_bot_token,
        api_base_url=settings.telegram_api_base_url,
    )
    consumer = UpdateConsumer(
        rabbitmq_url=settings.rabbitmq_url,
        exchange=settings.updates_exchange,
        queue=settings.updates_queue,
        shard_count=settings.updates_shards,
        handler=bot.handle_update,
    )

    await bot.start()
    await consumer.start()
    logger.info("worker running; press Ctrl+C to stop")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await stop.wait()
    finally:
        await consumer.stop()
        await bot.close()


__all__ = ["run_worker"]
