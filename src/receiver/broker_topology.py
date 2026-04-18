from __future__ import annotations

import logging
from dataclasses import dataclass

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

logger = logging.getLogger(__name__)

_SHARD_BINDING_WEIGHT = "1"


@dataclass(frozen=True)
class UpdatesTopology:
    exchange: AbstractExchange | None
    shard_queues: tuple[AbstractQueue, ...]

    @property
    def is_sharded(self) -> bool:
        return self.exchange is not None


async def declare_updates_topology(
    channel: AbstractChannel,
    *,
    exchange_name: str,
    shard_count: int,
    fallback_queue: str,
) -> UpdatesTopology:
    if not exchange_name:
        queue = await channel.declare_queue(fallback_queue, durable=True)
        logger.info(
            "Updates topology (single-queue): default exchange -> queue=%r",
            fallback_queue,
        )
        return UpdatesTopology(exchange=None, shard_queues=(queue,))

    exchange = await channel.declare_exchange(
        exchange_name,
        type="x-consistent-hash",
        durable=True,
    )

    shard_queues: list[AbstractQueue] = []
    for i in range(shard_count):
        queue_name = f"updates.shard.{i}"
        queue = await channel.declare_queue(queue_name, durable=True)
        await queue.bind(exchange, routing_key=_SHARD_BINDING_WEIGHT)
        shard_queues.append(queue)

    logger.info(
        "Updates topology (sharded): exchange=%r (x-consistent-hash), shards=%d",
        exchange_name,
        shard_count,
    )
    return UpdatesTopology(exchange=exchange, shard_queues=tuple(shard_queues))


__all__ = ["UpdatesTopology", "declare_updates_topology"]
