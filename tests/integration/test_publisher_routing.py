"""Publisher routing behaviour across Phase-2 and Phase-3 topologies.

We don't stand up a real RabbitMQ for these tests — aio-pika's channel,
exchange, and queue are mocked so the assertions stay focused on
whether the publisher picks the right exchange + routing_key for its
configured mode.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.receiver.publisher import PublisherError, UpdatePublisher


class _FakeExchange:
    """Mimics aio-pika's ``Exchange`` for assertion on publish calls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.publish = AsyncMock()


class _FakeQueue:
    def __init__(self, name: str) -> None:
        self.name = name
        self.bind = AsyncMock()


class _FakeChannel:
    """Records declare_* calls and hands back fake exchange/queue objects."""

    def __init__(self) -> None:
        self.declared_exchanges: list[tuple[str, str, bool]] = []
        self.declared_queues: list[tuple[str, bool]] = []
        self.default_exchange = _FakeExchange("")

    async def declare_exchange(
        self, name: str, *, type: str, durable: bool
    ) -> _FakeExchange:
        self.declared_exchanges.append((name, type, durable))
        return _FakeExchange(name)

    async def declare_queue(self, name: str, *, durable: bool) -> _FakeQueue:
        self.declared_queues.append((name, durable))
        return _FakeQueue(name)


class _FakeRobustConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self.is_closed = False
        self._channel = channel

    async def channel(self, publisher_confirms: bool = True) -> _FakeChannel:
        assert publisher_confirms is True
        return self._channel

    async def close(self) -> None:
        self.is_closed = True


@pytest.fixture
def patched_aio_pika(monkeypatch):
    """Patch out connect_robust / Message so we can run UpdatePublisher standalone."""
    channel = _FakeChannel()
    conn = _FakeRobustConnection(channel)

    async def _fake_connect_robust(url: str):  # noqa: ARG001
        return conn

    monkeypatch.setattr(
        "src.receiver.publisher.aio_pika.connect_robust", _fake_connect_robust
    )
    # Replace Message with a simple passthrough so publish() sees a
    # predictable object. We only assert on headers + routing_key.
    monkeypatch.setattr(
        "src.receiver.publisher.aio_pika.Message",
        lambda body, **kwargs: SimpleNamespace(body=body, **kwargs),
    )
    monkeypatch.setattr(
        "src.receiver.publisher.aio_pika.DeliveryMode",
        MagicMock(PERSISTENT=2),
    )
    return channel, conn


# ── Phase-2 mode (default exchange, single queue) ──────────────────────────


async def test_phase2_declares_only_fallback_queue(patched_aio_pika):
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher(
        "amqp://x", exchange="", queue="updates_queue", shard_count=4
    )
    await pub.connect()

    assert channel.declared_exchanges == []
    assert channel.declared_queues == [("updates_queue", True)]


async def test_phase2_publish_uses_default_exchange_and_queue_name(patched_aio_pika):
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher("amqp://x", exchange="", queue="updates_queue")
    await pub.connect()

    await pub.publish({"update_id": 1}, chat_id=42)

    channel.default_exchange.publish.assert_awaited_once()
    _message, kwargs = channel.default_exchange.publish.call_args
    assert kwargs["routing_key"] == "updates_queue"


# ── Phase-3 mode (x-consistent-hash exchange, N shards) ────────────────────


async def test_phase3_declares_exchange_and_all_shard_bindings(patched_aio_pika):
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher(
        "amqp://x", exchange="updates", queue="unused", shard_count=3
    )
    await pub.connect()

    assert channel.declared_exchanges == [("updates", "x-consistent-hash", True)]
    assert channel.declared_queues == [
        ("updates.shard.0", True),
        ("updates.shard.1", True),
        ("updates.shard.2", True),
    ]


async def test_phase3_publish_routes_by_chat_id(patched_aio_pika):
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher(
        "amqp://x", exchange="updates", queue="unused", shard_count=3
    )
    await pub.connect()

    topology_exchange = pub._topology.exchange  # type: ignore[union-attr]
    assert topology_exchange is not None

    await pub.publish({"update_id": 1}, chat_id=12345)
    await pub.publish({"update_id": 2}, chat_id=-67890)

    assert topology_exchange.publish.await_count == 2
    first_call = topology_exchange.publish.await_args_list[0].kwargs
    second_call = topology_exchange.publish.await_args_list[1].kwargs
    assert first_call["routing_key"] == "12345"
    assert second_call["routing_key"] == "-67890"


async def test_phase3_chat_less_update_routes_to_zero(patched_aio_pika):
    # Updates without a chat (polls, inline_query without from) must still
    # reach a shard — zero is the agreed fallback.
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher(
        "amqp://x", exchange="updates", queue="unused", shard_count=3
    )
    await pub.connect()

    topology_exchange = pub._topology.exchange  # type: ignore[union-attr]
    await pub.publish({"update_id": 7}, chat_id=None)

    topology_exchange.publish.assert_awaited_once()
    assert topology_exchange.publish.await_args.kwargs["routing_key"] == "0"


async def test_chat_id_travels_in_headers(patched_aio_pika):
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher(
        "amqp://x", exchange="updates", queue="unused", shard_count=3
    )
    await pub.connect()

    topology_exchange = pub._topology.exchange  # type: ignore[union-attr]
    await pub.publish({"update_id": 1}, chat_id=42)

    message, _kwargs = topology_exchange.publish.await_args
    assert message[0].headers == {"chat_id": 42}


async def test_publish_without_connect_raises(patched_aio_pika):
    pub = UpdatePublisher("amqp://x", exchange="updates", queue="q", shard_count=1)
    with pytest.raises(PublisherError, match="not connected"):
        await pub.publish({"update_id": 1}, chat_id=1)


async def test_broker_failure_surfaces_as_publisher_error(patched_aio_pika):
    channel, _conn = patched_aio_pika
    pub = UpdatePublisher(
        "amqp://x", exchange="updates", queue="unused", shard_count=1
    )
    await pub.connect()

    topology_exchange = pub._topology.exchange  # type: ignore[union-attr]
    topology_exchange.publish.side_effect = RuntimeError("broker ate it")

    with pytest.raises(PublisherError, match="publish failed"):
        await pub.publish({"update_id": 1}, chat_id=42)
