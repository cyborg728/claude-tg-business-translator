"""Broker topology declaration — Phase-2 fallback vs Phase-3 sharding."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tasks.broker_topology import declare_updates_topology


class _Channel:
    def __init__(self) -> None:
        self.declared_exchanges: list[tuple[str, str, bool]] = []
        self.declared_queues: list[tuple[str, bool]] = []

    async def declare_exchange(self, name, *, type, durable):
        self.declared_exchanges.append((name, type, durable))
        exchange = MagicMock(name=f"exchange-{name}")
        return exchange

    async def declare_queue(self, name, *, durable):
        self.declared_queues.append((name, durable))
        queue = MagicMock(name=f"queue-{name}")
        queue.bind = AsyncMock()
        return queue


async def test_empty_exchange_skips_shard_declarations():
    ch = _Channel()
    topo = await declare_updates_topology(
        ch, exchange_name="", shard_count=4, fallback_queue="updates_queue"
    )

    assert ch.declared_exchanges == []
    assert ch.declared_queues == [("updates_queue", True)]
    assert not topo.is_sharded
    assert topo.exchange is None
    assert len(topo.shard_queues) == 1


async def test_named_exchange_declares_consistent_hash_and_all_shards():
    ch = _Channel()
    topo = await declare_updates_topology(
        ch, exchange_name="updates", shard_count=4, fallback_queue="unused"
    )

    assert ch.declared_exchanges == [("updates", "x-consistent-hash", True)]
    assert ch.declared_queues == [
        ("updates.shard.0", True),
        ("updates.shard.1", True),
        ("updates.shard.2", True),
        ("updates.shard.3", True),
    ]
    assert topo.is_sharded
    assert len(topo.shard_queues) == 4


async def test_every_shard_binds_with_equal_weight():
    # Uniform weights are what make the consistent hash spread evenly —
    # catches a regression where someone used str(i) as the binding key.
    ch = _Channel()
    topo = await declare_updates_topology(
        ch, exchange_name="updates", shard_count=3, fallback_queue="unused"
    )

    for queue in topo.shard_queues:
        queue.bind.assert_awaited_once()
        _exchange_arg, kwargs = queue.bind.await_args
        assert kwargs["routing_key"] == "1"


async def test_shard_count_of_one_still_works():
    # Edge case — a dev might set UPDATES_SHARDS=1 for simplicity. The
    # hash exchange still distributes, just trivially.
    ch = _Channel()
    topo = await declare_updates_topology(
        ch, exchange_name="updates", shard_count=1, fallback_queue="unused"
    )
    assert len(topo.shard_queues) == 1
