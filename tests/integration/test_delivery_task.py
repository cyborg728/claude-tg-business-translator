"""Integration tests for the generic ``deliver`` Celery task.

We mock the two external dependencies (Redis and httpx.Client) and run the
task synchronously via ``.apply()``. This exercises rate-limit acquisition,
payload routing, and response handling without touching the real broker.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry

from src.tasks import delivery


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")


class _FakeHttpClient:
    """Enough of ``httpx.Client`` to satisfy ``deliver``."""

    def __init__(self, response: _FakeResponse):
        self.response = response
        self.last_url: str | None = None
        self.last_payload: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json):
        self.last_url = url
        self.last_payload = json
        return self.response


@pytest.fixture
def fake_redis_sync():
    """Patch ``_sync_redis`` to return a MagicMock that always grants slots."""
    r = MagicMock()
    pipe = MagicMock()
    # Allow unlimited slots: INCR returns 1 every time.
    pipe.execute.return_value = (1, True)
    r.pipeline.return_value = pipe
    with patch.object(delivery, "_sync_redis", return_value=r):
        yield r


def _run_deliver(*, method, payload):
    """Invoke the task synchronously and propagate exceptions.

    We call ``run`` (the underlying function with ``self`` bound) rather
    than ``apply``, because apply() wraps ``Retry`` into eager-retry
    bookkeeping and swallows it — we want to see exceptions raw.
    """
    return delivery.deliver.run(method=method, payload=payload)


# ── Happy paths ──────────────────────────────────────────────────────────────


def test_deliver_send_message_success(fake_redis_sync):
    fake_http = _FakeHttpClient(_FakeResponse(200, {"ok": True, "result": {"message_id": 9}}))
    with patch("httpx.Client", return_value=fake_http):
        result = _run_deliver(
            method="sendMessage",
            payload={"chat_id": 42, "text": "hi", "parse_mode": "HTML"},
        )

    assert result == {"message_id": 9}
    assert fake_http.last_url.endswith("/sendMessage")
    assert fake_http.last_payload == {"chat_id": 42, "text": "hi", "parse_mode": "HTML"}


def test_deliver_send_photo_routes_to_correct_method(fake_redis_sync):
    fake_http = _FakeHttpClient(_FakeResponse(200, {"ok": True, "result": {}}))
    with patch("httpx.Client", return_value=fake_http):
        _run_deliver(
            method="sendPhoto",
            payload={"chat_id": 42, "photo": "file_id_x", "caption": "hi"},
        )
    assert fake_http.last_url.endswith("/sendPhoto")
    assert fake_http.last_payload["photo"] == "file_id_x"


def test_deliver_with_reply_markup_passes_buttons_through(fake_redis_sync):
    markup = {"inline_keyboard": [[{"text": "go", "callback_data": "x"}]]}
    fake_http = _FakeHttpClient(_FakeResponse(200, {"ok": True, "result": {}}))
    with patch("httpx.Client", return_value=fake_http):
        _run_deliver(
            method="sendMessage",
            payload={"chat_id": 1, "text": "pick", "reply_markup": markup},
        )
    assert fake_http.last_payload["reply_markup"] == markup


# ── Rate limiting ────────────────────────────────────────────────────────────


def test_deliver_acquires_chat_slot_for_chatful_method(fake_redis_sync):
    fake_http = _FakeHttpClient(_FakeResponse(200, {"ok": True, "result": {}}))
    with patch("httpx.Client", return_value=fake_http):
        _run_deliver(method="sendMessage", payload={"chat_id": 777, "text": "hi"})

    # Bucket keys passed to INCR should include both the global and per-chat key.
    incr_keys = [call.args[0] for call in fake_redis_sync.pipeline.return_value.incr.call_args_list]
    assert any(k.startswith("rl:global:") for k in incr_keys)
    assert any(k.startswith("rl:chat:777:") for k in incr_keys)


def test_deliver_skips_chat_slot_for_chatless_method(fake_redis_sync):
    fake_http = _FakeHttpClient(_FakeResponse(200, {"ok": True, "result": True}))
    with patch("httpx.Client", return_value=fake_http):
        _run_deliver(
            method="answerCallbackQuery",
            payload={"callback_query_id": "qid", "text": "ack"},
        )

    incr_keys = [call.args[0] for call in fake_redis_sync.pipeline.return_value.incr.call_args_list]
    assert any(k.startswith("rl:global:") for k in incr_keys)
    assert not any(k.startswith("rl:chat:") for k in incr_keys)


# ── Error handling ───────────────────────────────────────────────────────────


def test_deliver_raises_retry_on_429(fake_redis_sync):
    fake_http = _FakeHttpClient(
        _FakeResponse(429, {"ok": False, "parameters": {"retry_after": 7}})
    )
    with patch("httpx.Client", return_value=fake_http):
        # celery's autoretry_for catches Retry and re-raises after max_retries;
        # in apply() mode we see Retry directly.
        with pytest.raises(Retry) as exc_info:
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
    # Celery stores the requested delay in ``.when`` (seconds from now) or as a
    # datetime; we only care it matches our retry_after.
    assert getattr(exc_info.value, "when", None) == 7


def test_deliver_raises_on_server_error(fake_redis_sync):
    fake_http = _FakeHttpClient(_FakeResponse(500, text="boom"))
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(RuntimeError, match="HTTP 500"):
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
