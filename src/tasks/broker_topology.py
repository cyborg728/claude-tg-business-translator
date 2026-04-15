"""RabbitMQ topology declaration for v3 Phase-3 update routing.

One function declares the entire producer/consumer topology so the
receiver, the consumer worker's bootstrap, and tests agree on exchange
and queue names. Idempotent — safe to call on every reconnect.

Layout
------
* Exchange ``UPDATES_EXCHANGE`` (default ``updates``), type
  ``x-consistent-hash``, durable.
* Shard queues ``updates.shard.0`` .. ``updates.shard.<N-1>`` where
  ``N = UPDATES_SHARDS``. Each queue durable, bound to the exchange
  with routing key ``"1"`` (the weight — equal weights mean uniform
  distribution across the hash ring).

Phase-2 compatibility
---------------------
If ``UPDATES_EXCHANGE`` is empty the helper skips exchange/binding
declarations and only ensures ``UPDATES_QUEUE`` exists. This keeps the
Phase-2 ``scaled`` overlay working without the
``rabbitmq_consistent_hash_exchange`` plugin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

logger = logging.getLogger(__name__)

# Equal weight per shard — RabbitMQ's x-consistent-hash uses the routing
# key on bindings as the weight. All shards equal => uniform hash ring.
_SHARD_BINDING_WEIGHT = "1"


@dataclass(frozen=True)
class UpdatesTopology:
    """Everything a producer or consumer needs to talk about updates."""

    exchange: AbstractExchange | None  # None in Phase-2 default-exchange mode
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
    """Declare exchange + shard queues (Phase 3) or a single queue (Phase 2).

    Returns the live aio-pika objects so callers can publish / consume
    without re-declaring anything. Declarations are passive where
    possible — aio-pika's default ``durable=True`` + server-idempotent
    ``queue.declare`` means it's safe to run on every reconnect.
    """
    if not exchange_name:
        # Phase 2: default direct exchange, single queue.
        queue = await channel.declare_queue(fallback_queue, durable=True)
        logger.info(
            "Updates topology (phase-2): default exchange → queue=%r",
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
        # Weight of "1" on every binding → uniform distribution across
        # the hash ring. Change this if you ever want to bias shards.
        await queue.bind(exchange, routing_key=_SHARD_BINDING_WEIGHT)
        shard_queues.append(queue)

    logger.info(
        "Updates topology (phase-3): exchange=%r (x-consistent-hash), "
        "shards=%d",
        exchange_name,
        shard_count,
    )
    return UpdatesTopology(exchange=exchange, shard_queues=tuple(shard_queues))


__all__ = [
    "UpdatesTopology",
    "declare_updates_topology",
]
