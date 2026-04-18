from __future__ import annotations

from typing import Any

import pytest_asyncio
from litestar.testing import TestClient
from prometheus_client import REGISTRY

from src.config import Settings
from src.receiver.app import create_app
from src.receiver.publisher import PublisherError
from src.metrics import receiver_publish_duration_seconds, receiver_requests_total


class _StubPublisher:
    def __init__(self) -> None:
        self.connected = True
        self.raise_on_publish: Exception | None = None
        self.calls: list[tuple[dict[str, Any], int | None]] = []

    def is_connected(self) -> bool:
        return self.connected

    async def publish(self, update: dict[str, Any], *, chat_id: int | None) -> None:
        if self.raise_on_publish is not None:
            raise self.raise_on_publish
        self.calls.append((update, chat_id))


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "telegram_bot_token": "12345:test",
        "webhook_base_url": "https://example.test",
        "webhook_secret_token": "s3cret",
        "dedup_ttl_seconds": 60,
        "updates_queue": "updates_queue",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _update(update_id: int = 1, chat_id: int = 42) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 100,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "U"},
            "text": "hi",
        },
    }


@pytest_asyncio.fixture
async def metrics_receiver(fake_redis):
    publisher = _StubPublisher()
    settings = _settings()
    app = create_app(settings, fake_redis, publisher)  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client, publisher, settings


def _counter_value(name: str, labels: dict[str, str]) -> float:
    for metric in REGISTRY.collect():
        if metric.name == name:
            for sample in metric.samples:
                if sample.name == f"{name}_total" and all(
                    sample.labels.get(k) == v for k, v in labels.items()
                ):
                    return sample.value
    return 0.0


# ── /metrics endpoint ─────────────────────────────────────────────────────


async def test_metrics_endpoint_returns_prometheus_format(metrics_receiver):
    client, _pub, _s = metrics_receiver
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "dedup_hit_total" in body
    assert "receiver_requests_total" in body
    assert "receiver_publish_duration_seconds" in body


# ── receiver_requests_total counter ───────────────────────────────────────


async def test_published_request_increments_counter(metrics_receiver):
    client, _pub, settings = metrics_receiver
    before = _counter_value("receiver_requests", {"outcome": "published"})

    client.post(
        settings.webhook_path,
        json=_update(update_id=9001),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )

    after = _counter_value("receiver_requests", {"outcome": "published"})
    assert after == before + 1


async def test_dedup_request_increments_counter(metrics_receiver):
    client, _pub, settings = metrics_receiver
    headers = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}

    client.post(settings.webhook_path, json=_update(update_id=9002), headers=headers)
    before = _counter_value("receiver_requests", {"outcome": "dedup"})

    client.post(settings.webhook_path, json=_update(update_id=9002), headers=headers)
    after = _counter_value("receiver_requests", {"outcome": "dedup"})
    assert after == before + 1


async def test_publish_error_increments_counter(metrics_receiver):
    client, publisher, settings = metrics_receiver
    publisher.raise_on_publish = PublisherError("broker down")
    before = _counter_value("receiver_requests", {"outcome": "publish_error"})

    client.post(
        settings.webhook_path,
        json=_update(update_id=9003),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )

    after = _counter_value("receiver_requests", {"outcome": "publish_error"})
    assert after == before + 1


# ── receiver_publish_duration_seconds histogram ───────────────────────────


async def test_publish_duration_histogram_observes(metrics_receiver):
    client, _pub, settings = metrics_receiver
    before = receiver_publish_duration_seconds._sum.get()

    client.post(
        settings.webhook_path,
        json=_update(update_id=9004),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )

    after = receiver_publish_duration_seconds._sum.get()
    assert after > before
