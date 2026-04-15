"""Integration tests for the generic ``deliver`` Celery task.

We mock the two external dependencies (Redis and httpx.Client) and run the
task synchronously via ``.apply()``. This exercises rate-limit acquisition,
payload routing, and response handling without touching the real broker.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry

from src.tasks import delivery, metrics


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


def test_deliver_honors_top_level_retry_after(fake_redis_sync):
    # Older Bot API responses put ``retry_after`` at the top level; newer
    # ones nest it under ``parameters``. Either should work.
    fake_http = _FakeHttpClient(_FakeResponse(429, {"ok": False, "retry_after": 3}))
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(Retry) as exc_info:
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
    assert getattr(exc_info.value, "when", None) == 3


def test_deliver_picks_larger_retry_after_when_both_present(fake_redis_sync):
    fake_http = _FakeHttpClient(
        _FakeResponse(429, {"ok": False, "retry_after": 2, "parameters": {"retry_after": 9}})
    )
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(Retry) as exc_info:
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
    assert getattr(exc_info.value, "when", None) == 9


def test_deliver_raises_on_server_error(fake_redis_sync):
    # 5xx raises a plain RuntimeError — Celery's ``autoretry_for`` catches
    # it in worker context and reschedules. Under ``.run()`` we see the
    # original RuntimeError (because autoretry's internal ``self.retry``
    # detects ``called_directly=True`` and re-raises the exception).
    fake_http = _FakeHttpClient(_FakeResponse(500, text="boom"))
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(RuntimeError, match="Telegram 5xx"):
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})


def test_deliver_raises_on_client_error(fake_redis_sync):
    # 4xx (non-429) is not retryable — ``on_failure`` DLQs it.
    fake_http = _FakeHttpClient(_FakeResponse(400, text="bad"))
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(RuntimeError, match="HTTP 400"):
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})


# ── Metrics ──────────────────────────────────────────────────────────────────


def _counter_value(counter, **labels):
    return counter.labels(**labels)._value.get()


def test_deliver_increments_sent_metric_on_success(fake_redis_sync):
    before = _counter_value(metrics.deliver_sent_total, method="sendMessage")
    fake_http = _FakeHttpClient(_FakeResponse(200, {"ok": True, "result": {}}))
    with patch("httpx.Client", return_value=fake_http):
        _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
    after = _counter_value(metrics.deliver_sent_total, method="sendMessage")
    assert after == before + 1


def test_deliver_increments_throttled_and_retry_metrics_on_429(fake_redis_sync):
    before_429 = _counter_value(metrics.deliver_throttled_total, method="sendMessage")
    before_retry = _counter_value(
        metrics.deliver_retried_total, method="sendMessage", reason="throttled"
    )
    fake_http = _FakeHttpClient(_FakeResponse(429, {"retry_after": 1}))
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(Retry):
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
    assert (
        _counter_value(metrics.deliver_throttled_total, method="sendMessage")
        == before_429 + 1
    )
    assert (
        _counter_value(metrics.deliver_retried_total, method="sendMessage", reason="throttled")
        == before_retry + 1
    )


def test_deliver_increments_server_error_metric_on_5xx(fake_redis_sync):
    before = _counter_value(metrics.deliver_server_error_total, method="sendMessage")
    fake_http = _FakeHttpClient(_FakeResponse(500, text="boom"))
    with patch("httpx.Client", return_value=fake_http):
        with pytest.raises(RuntimeError):
            _run_deliver(method="sendMessage", payload={"chat_id": 1, "text": "hi"})
    assert (
        _counter_value(metrics.deliver_server_error_total, method="sendMessage")
        == before + 1
    )


# ── Dead-lettering ───────────────────────────────────────────────────────────


def test_on_failure_publishes_to_dlq_and_increments_metric():
    """Simulate Celery calling ``on_failure`` after retries exhausted."""
    before = _counter_value(
        metrics.deliver_dead_lettered_total, method="sendMessage", reason="RuntimeError"
    )

    captured = {}

    def fake_publish(**kwargs):
        captured.update(kwargs)

    task = delivery.deliver
    with patch.object(delivery, "_publish_dlq", side_effect=fake_publish):
        task.on_failure(
            exc=RuntimeError("5xx exhausted"),
            task_id="abc-123",
            args=(),
            kwargs={"method": "sendMessage", "payload": {"chat_id": 42, "text": "x"}},
            einfo=None,
        )

    assert captured["method"] == "sendMessage"
    assert captured["payload"] == {"chat_id": 42, "text": "x"}
    assert captured["reason"] == "RuntimeError"
    assert captured["task_id"] == "abc-123"
    after = _counter_value(
        metrics.deliver_dead_lettered_total, method="sendMessage", reason="RuntimeError"
    )
    assert after == before + 1


def test_on_failure_never_raises_when_dlq_publish_fails():
    """A broken DLQ must not mask the original task failure."""
    task = delivery.deliver
    with patch.object(delivery, "_publish_dlq", side_effect=OSError("broker down")):
        # Should complete without re-raising.
        task.on_failure(
            exc=RuntimeError("boom"),
            task_id="t",
            args=(),
            kwargs={"method": "sendMessage", "payload": {"chat_id": 1}},
            einfo=None,
        )


def test_retry_exception_bypasses_on_failure():
    """``Retry`` is control-flow — it must never trigger DLQ publishing."""
    # We don't need to invoke on_failure for Retry; Celery already skips it
    # because ``Retry`` is listed in ``throws``. Here we just lock the
    # invariant so no one removes ``throws = (Retry,)`` without thinking.
    assert Retry in delivery._DeliveryTask.throws
