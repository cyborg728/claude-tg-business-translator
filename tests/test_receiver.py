from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from litestar.testing import TestClient

from src.config import Settings
from src.receiver.app import create_app
from src.receiver.publisher import PublisherError


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


@pytest_asyncio.fixture
async def receiver(fake_redis):
    publisher = _StubPublisher()
    settings = _settings()
    app = create_app(settings, fake_redis, publisher)  # type: ignore[arg-type]
    with TestClient(app) as client:
        yield client, publisher, settings


def _update(update_id: int = 1, chat_id: int | None = 42) -> dict[str, Any]:
    payload: dict[str, Any] = {"update_id": update_id}
    if chat_id is not None:
        payload["message"] = {
            "message_id": 100,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "U"},
            "text": "hi",
        }
    return payload


# ── Webhook endpoint ───────────────────────────────────────────────────────


async def test_happy_path_publishes_and_returns_200(receiver):
    client, publisher, settings = receiver
    resp = client.post(
        settings.webhook_path,
        json=_update(update_id=1, chat_id=42),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status_code == 200
    assert len(publisher.calls) == 1
    payload, chat_id = publisher.calls[0]
    assert payload["update_id"] == 1
    assert chat_id == 42


async def test_bad_secret_token_is_401(receiver):
    client, publisher, settings = receiver
    resp = client.post(
        settings.webhook_path,
        json=_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status_code == 401
    assert publisher.calls == []


async def test_missing_secret_token_is_401(receiver):
    client, publisher, settings = receiver
    resp = client.post(settings.webhook_path, json=_update())
    assert resp.status_code == 401
    assert publisher.calls == []


async def test_empty_configured_secret_disables_check(fake_redis):
    publisher = _StubPublisher()
    settings = _settings(webhook_secret_token="")
    app = create_app(settings, fake_redis, publisher)  # type: ignore[arg-type]
    with TestClient(app) as client:
        resp = client.post(settings.webhook_path, json=_update(update_id=7))
    assert resp.status_code == 200
    assert len(publisher.calls) == 1


async def test_missing_update_id_is_400(receiver):
    client, publisher, settings = receiver
    resp = client.post(
        settings.webhook_path,
        json={"message": {"chat": {"id": 1}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status_code == 400
    assert publisher.calls == []


async def test_duplicate_update_id_is_200_and_does_not_publish(receiver):
    client, publisher, settings = receiver
    headers = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}

    first = client.post(settings.webhook_path, json=_update(update_id=5), headers=headers)
    second = client.post(settings.webhook_path, json=_update(update_id=5), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(publisher.calls) == 1


async def test_publisher_error_is_503(receiver):
    client, publisher, settings = receiver
    publisher.raise_on_publish = PublisherError("broker down")
    resp = client.post(
        settings.webhook_path,
        json=_update(update_id=9),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status_code == 503


async def test_update_without_chat_publishes_with_none(fake_redis):
    publisher = _StubPublisher()
    settings = _settings()
    app = create_app(settings, fake_redis, publisher)  # type: ignore[arg-type]
    payload = {"update_id": 100, "poll": {"id": "x", "question": "?"}}
    with TestClient(app) as client:
        resp = client.post(
            settings.webhook_path,
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
        )
    assert resp.status_code == 200
    assert publisher.calls == [(payload, None)]


# ── Health / readiness ─────────────────────────────────────────────────────


async def test_healthz_always_ok(receiver):
    client, _publisher, _settings = receiver
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_ok_when_deps_healthy(receiver):
    client, _publisher, _settings = receiver
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_503_when_broker_disconnected(receiver):
    client, publisher, _settings = receiver
    publisher.connected = False
    resp = client.get("/readyz")
    assert resp.status_code == 503


async def test_readyz_503_when_redis_fails(fake_redis):
    publisher = _StubPublisher()
    settings = _settings()

    class _BrokenRedis:
        async def ping(self) -> None:
            raise RuntimeError("redis down")

    app = create_app(settings, _BrokenRedis(), publisher)  # type: ignore[arg-type]
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
