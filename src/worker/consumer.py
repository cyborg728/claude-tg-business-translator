from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractQueue, AbstractRobustConnection

from src.receiver.broker_topology import UpdatesTopology, declare_updates_topology

logger = logging.getLogger(__name__)

UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]


class UpdateConsumer:
    def __init__(
        self,
        rabbitmq_url: str,
        *,
        exchange: str,
        queue: str,
        shard_count: int,
        handler: UpdateHandler,
        prefetch: int = 1,
    ) -> None:
        self._url = rabbitmq_url
        self._exchange_name = exchange
        self._queue_name = queue
        self._shard_count = shard_count
        self._handler = handler
        self._prefetch = prefetch

        self._conn: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._topology: UpdatesTopology | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        self._conn = await aio_pika.connect_robust(self._url)
        self._channel = await self._conn.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch)

        self._topology = await declare_updates_topology(
            self._channel,
            exchange_name=self._exchange_name,
            shard_count=self._shard_count,
            fallback_queue=self._queue_name,
        )

        for queue in self._topology.shard_queues:
            task = asyncio.create_task(
                self._consume(queue), name=f"consumer:{queue.name}"
            )
            self._tasks.append(task)

        logger.info(
            "UpdateConsumer started (queues=%d, prefetch=%d, mode=%s)",
            len(self._tasks),
            self._prefetch,
            "sharded" if self._topology.is_sharded else "single-queue",
        )

    async def wait_closed(self) -> None:
        if not self._tasks:
            return
        await self._stopping.wait()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._conn is not None and not self._conn.is_closed:
            await self._conn.close()
        self._conn = None
        self._channel = None
        self._topology = None

    async def _consume(self, queue: AbstractQueue) -> None:
        logger.info("Consuming queue=%s", queue.name)
        async with queue.iterator() as iterator:
            async for message in iterator:
                try:
                    async with message.process(requeue=False):
                        await self._dispatch(message.body)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Handler crashed; message dropped")

    async def _dispatch(self, body: bytes) -> None:
        try:
            update = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Skipping non-JSON message (%d bytes)", len(body))
            return
        if not isinstance(update, dict):
            logger.warning("Skipping non-object update: %r", type(update).__name__)
            return
        await self._handler(update)


__all__ = ["UpdateConsumer", "UpdateHandler"]
