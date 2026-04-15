"""Async RabbitMQ publisher for incoming Telegram updates.

Used exclusively by the :mod:`src.receiver` FastAPI app.

Topology
--------
The publisher delegates topology declaration to
:func:`src.tasks.broker_topology.declare_updates_topology`, so the
receiver and the update-consumer agree on exchange/queue names.

* **Phase 2** (``UPDATES_EXCHANGE=""``) — built-in default direct
  exchange, single ``UPDATES_QUEUE``. ``chat_id`` travels in headers
  only; the routing key is the queue name.
* **Phase 3** (``UPDATES_EXCHANGE`` non-empty) — named
  ``x-consistent-hash`` exchange, ``N = UPDATES_SHARDS`` bound shard
  queues, routing key is ``str(chat_id)``. Updates without a chat fall
  back to ``"0"`` so they still land somewhere deterministic.

Robust connection
-----------------
``aio_pika.connect_robust`` auto-reconnects on broker flaps. Channels
with ``publisher_confirms=True`` give us at-least-once semantics —
:meth:`publish` returns only after the broker ACKs the message.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

from src.tasks.broker_topology import UpdatesTopology, declare_updates_topology

logger = logging.getLogger(__name__)


class PublisherError(RuntimeError):
    """Raised when publishing fails for any reason the caller should surface."""


class UpdatePublisher:
    """Durable async publisher for Telegram update payloads."""

    def __init__(
        self,
        rabbitmq_url: str,
        *,
        exchange: str,
        queue: str,
        shard_count: int = 1,
    ) -> None:
        self._url = rabbitmq_url
        self._exchange_name = exchange
        self._queue_name = queue
        self._shard_count = shard_count
        self._conn: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._topology: UpdatesTopology | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open a robust connection and declare the publish topology."""
        self._conn = await aio_pika.connect_robust(self._url)
        self._channel = await self._conn.channel(publisher_confirms=True)
        self._topology = await declare_updates_topology(
            self._channel,
            exchange_name=self._exchange_name,
            shard_count=self._shard_count,
            fallback_queue=self._queue_name,
        )
        logger.info(
            "UpdatePublisher ready (mode=%s)",
            "sharded" if self._topology.is_sharded else "single-queue",
        )

    async def close(self) -> None:
        if self._conn is not None and not self._conn.is_closed:
            await self._conn.close()
        self._conn = None
        self._channel = None
        self._topology = None

    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closed

    # ── publish ────────────────────────────────────────────────────────────

    async def publish(self, update: dict[str, Any], *, chat_id: int | None) -> None:
        """Publish the raw update JSON. Raises :class:`PublisherError` on any
        broker-side failure so the FastAPI handler can translate it to 503."""
        if self._channel is None or self._topology is None:
            raise PublisherError("publisher not connected")

        body = json.dumps(update, ensure_ascii=False).encode("utf-8")
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            # chat_id in headers → Phase-3 consumers don't need to re-parse
            # the body to know which chat this belongs to (handy for logs
            # and metrics labels).
            headers={"chat_id": chat_id} if chat_id is not None else None,
        )

        if self._topology.is_sharded:
            # Phase 3: named x-consistent-hash exchange. Routing key is the
            # chat id as a string; chat-less updates use "0" so they still
            # hit a shard (they'll pile on one shard but that's rare traffic).
            exchange = self._topology.exchange
            assert exchange is not None  # for type-checker; is_sharded guards
            routing_key = str(chat_id) if chat_id is not None else "0"
        else:
            # Phase 2: default exchange, routing key == queue name.
            exchange = self._channel.default_exchange
            routing_key = self._queue_name

        try:
            await exchange.publish(message, routing_key=routing_key)
        except Exception as exc:  # broad — any aio-pika/amqp error is fatal here
            raise PublisherError(f"publish failed: {exc}") from exc


__all__ = ["PublisherError", "UpdatePublisher"]
