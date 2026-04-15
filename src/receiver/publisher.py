"""Async RabbitMQ publisher for incoming Telegram updates.

Used exclusively by the :mod:`src.receiver` FastAPI app. The bot-side
(PTB) path does not publish — it still processes updates in-process
until Phase 3 flips everyone to queue-based dispatch.

Phase 2 vs Phase 3 topology
---------------------------
* **Phase 2 (now)** — single queue ``updates_queue``, published via the
  built-in default direct exchange (``exchange=""``). One consumer
  somewhere (old ``bot`` Deployment still handling updates directly, or
  a stub worker) is enough to make this end-to-end once Phase 3 wires
  consumption.
* **Phase 3** — replace with an ``x-consistent-hash`` exchange and N
  shard queues. This class will grow a ``chat_id`` routing-key branch;
  callers already pass ``chat_id`` in so nothing changes at the call
  site.

The publisher uses :func:`aio_pika.connect_robust` — the connection
auto-reconnects on broker flaps and the channel is re-established
transparently. Callers treat the publish as a simple awaitable; failure
surfaces as :class:`PublisherError`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

logger = logging.getLogger(__name__)


class PublisherError(RuntimeError):
    """Raised when publishing fails for any reason the caller should surface."""


class UpdatePublisher:
    """Durable async publisher for Telegram update payloads."""

    def __init__(self, rabbitmq_url: str, exchange: str, queue: str) -> None:
        self._url = rabbitmq_url
        self._exchange_name = exchange
        self._queue_name = queue
        self._conn: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open a robust connection and declare the target queue."""
        self._conn = await aio_pika.connect_robust(self._url)
        self._channel = await self._conn.channel(publisher_confirms=True)
        # Declare the queue so the first publish can't land in a void.
        # ``durable=True`` ensures messages survive broker restarts.
        await self._channel.declare_queue(self._queue_name, durable=True)
        logger.info(
            "UpdatePublisher ready (exchange=%r queue=%r)",
            self._exchange_name,
            self._queue_name,
        )

    async def close(self) -> None:
        if self._conn is not None and not self._conn.is_closed:
            await self._conn.close()
        self._conn = None
        self._channel = None

    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closed

    # ── publish ────────────────────────────────────────────────────────────

    async def publish(self, update: dict[str, Any], *, chat_id: int | None) -> None:
        """Publish the raw update JSON. ``chat_id`` drives sharding in Phase 3.

        Raises :class:`PublisherError` on any broker-side failure so the
        caller (FastAPI handler) can translate it to HTTP 503.
        """
        if self._channel is None:
            raise PublisherError("publisher not connected")

        body = json.dumps(update, ensure_ascii=False).encode("utf-8")
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"chat_id": chat_id} if chat_id is not None else None,
        )

        # Phase 2: use the default exchange, routing key = queue name.
        # Phase 3: will switch to named x-consistent-hash exchange with
        # ``routing_key=str(chat_id)`` — the call sites don't need to change.
        exchange = (
            await self._channel.get_exchange(self._exchange_name)
            if self._exchange_name
            else self._channel.default_exchange
        )
        routing_key = self._queue_name if not self._exchange_name else str(chat_id or 0)

        try:
            await exchange.publish(message, routing_key=routing_key)
        except Exception as exc:  # broad — any aio-pika/amqp error is fatal here
            raise PublisherError(f"publish failed: {exc}") from exc


__all__ = ["PublisherError", "UpdatePublisher"]
