from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

from .broker_topology import UpdatesTopology, declare_updates_topology

logger = logging.getLogger(__name__)


class PublisherError(RuntimeError):
    pass


class UpdatePublisher:
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

    async def connect(self) -> None:
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

    async def publish(self, update: dict[str, Any], *, chat_id: int | None) -> None:
        if self._channel is None or self._topology is None:
            raise PublisherError("publisher not connected")

        body = json.dumps(update, ensure_ascii=False).encode("utf-8")
        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"chat_id": chat_id} if chat_id is not None else None,
        )

        if self._topology.is_sharded:
            exchange = self._topology.exchange
            assert exchange is not None
            routing_key = str(chat_id) if chat_id is not None else "0"
        else:
            exchange = self._channel.default_exchange
            routing_key = self._queue_name

        try:
            await exchange.publish(message, routing_key=routing_key)
        except Exception as exc:
            raise PublisherError(f"publish failed: {exc}") from exc


__all__ = ["PublisherError", "UpdatePublisher"]
