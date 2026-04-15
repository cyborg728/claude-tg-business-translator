"""Rate-limited Telegram delivery worker (consumes *delivery_queue*).

Public surface
--------------
* :func:`deliver` — **generic** Celery task. Accepts any Bot API method
  (``sendMessage``, ``sendPhoto``, ``editMessageText``, …) and a payload
  dict. Rate-limits per-chat and globally, retries on 429 / 5xx.
* :func:`send_text`, :func:`send_photo`, :func:`edit_text` — thin
  **facade** helpers on top of :func:`deliver`. Callers use these instead
  of assembling raw payloads — they never touch the ``method`` string.

Telegram API limits
-------------------
* **Global**: ~30 messages/second across the whole bot.
* **Per chat**: ~1 message/second (group chats slightly higher).

We keep two rolling-window counters in Redis:

* ``rl:global``       — a sliding second shared by all tasks.
* ``rl:chat:<id>``    — per chat counter.

If a limit would be exceeded we ``time.sleep`` briefly and retry the check.
Celery's ``task_reject_on_worker_lost + acks_late`` settings guarantee the
message is re-delivered in case the worker dies mid-sleep.

Dead-lettering
--------------
When a delivery task exhausts ``max_retries`` (5xx from Bot API, broker
errors, etc.) Celery calls :meth:`_DeliveryTask.on_failure`, which
publishes a **raw** record (method, payload, reason, traceback) onto the
``delivery_dlq`` queue. There is no consumer — operators drain it
manually (CLI / rabbitmqadmin). 429 responses are **not** dead-lettered;
they're purely back-pressure and re-enqueue themselves via ``Retry``.

Adding a new Bot API method
---------------------------
You almost certainly don't need a new task — just call :func:`deliver`
directly, or add a one-line facade helper. Files, buttons and media groups
all travel through the same ``method + payload`` channel, because every
Bot API endpoint is ``POST /bot<token>/<method>`` with a JSON body.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from celery import Task
from celery.exceptions import Retry
from kombu import Queue

from src.config import get_settings

from . import metrics
from .celery_app import celery_app

logger = logging.getLogger(__name__)

_GLOBAL_KEY = "rl:global"
_CHAT_KEY_PREFIX = "rl:chat:"

# Bot API methods that don't target a specific chat (no per-chat budget).
_CHATLESS_METHODS = frozenset({"answerCallbackQuery", "answerInlineQuery"})


def _sync_redis():
    """Build a sync Redis client on demand (Celery workers are sync)."""
    import redis  # local import keeps bot-side imports cheap

    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def _try_acquire(r, key: str, limit_per_second: int) -> bool:
    """Atomic "bucket in this second" counter."""
    now_s = int(time.time())
    bucket = f"{key}:{now_s}"
    pipe = r.pipeline()
    pipe.incr(bucket, 1)
    pipe.expire(bucket, 2)  # survive the boundary with a 1-second safety net
    count, _ = pipe.execute()
    return int(count) <= limit_per_second


def _wait_for_slot(r, chat_id: int | None) -> None:
    """Block until we have a global slot (and a per-chat slot if ``chat_id``)."""
    settings = get_settings()
    max_wait_s = 5.0
    elapsed = 0.0
    sleep_step = 0.05

    while elapsed < max_wait_s:
        global_ok = _try_acquire(r, _GLOBAL_KEY, settings.delivery_rate_per_second)
        chat_ok = (
            _try_acquire(
                r, f"{_CHAT_KEY_PREFIX}{chat_id}", settings.delivery_rate_per_chat
            )
            if chat_id is not None
            else True
        )
        if global_ok and chat_ok:
            return
        time.sleep(sleep_step)
        elapsed += sleep_step
    # Reached max-wait — let Celery retry with backoff.
    raise Retry(when=1)


def _retry_after_from_429(body: dict) -> int:
    """Pick the ``retry_after`` from a 429 body, honoring both layouts.

    Telegram historically puts the hint at the top level (``retry_after``)
    and now also at ``parameters.retry_after``. Whichever is present (and
    larger) wins; fall back to 1 second if neither is present.
    """
    top = body.get("retry_after")
    nested = (body.get("parameters") or {}).get("retry_after")
    candidates = [int(v) for v in (top, nested) if v is not None]
    return max(candidates) if candidates else 1


def _publish_dlq(
    *,
    method: str,
    payload: dict[str, Any],
    reason: str,
    task_id: str | None,
    traceback: str | None,
) -> None:
    """Publish a raw record to ``delivery_dlq`` — no task, no consumer."""
    settings = get_settings()
    record = {
        "method": method,
        "payload": payload,
        "reason": reason,
        "task_id": task_id,
        "traceback": traceback,
        "ts": int(time.time()),
    }
    # ``producer_pool.acquire`` reuses broker connections from the Celery
    # app's pool; ``declare`` ensures the queue exists before publish
    # (worker already declared it on startup via ``task_queues``, but this
    # makes the helper safe to call from anywhere).
    with celery_app.producer_pool.acquire(block=True) as producer:
        producer.publish(
            record,
            serializer="json",
            routing_key=settings.queue_delivery_dlq,
            declare=[Queue(settings.queue_delivery_dlq)],
        )


# ── Task class: dead-letter on terminal failure ─────────────────────────────


class _DeliveryTask(Task):
    """Custom base so terminal failures land in the DLQ with a trace."""

    # ``Retry`` is a control-flow exception — Celery uses it to reschedule.
    # We never want the DLQ hook to fire on Retry.
    throws = (Retry,)

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # type: ignore[override]
        method = kwargs.get("method") or (args[0] if args else "?")
        payload = kwargs.get("payload") or (args[1] if len(args) > 1 else {})
        reason = type(exc).__name__
        logger.error(
            "DLQ: method=%s chat_id=%s reason=%s task_id=%s",
            method,
            payload.get("chat_id") if isinstance(payload, dict) else None,
            reason,
            task_id,
        )
        try:
            _publish_dlq(
                method=method,
                payload=payload if isinstance(payload, dict) else {},
                reason=reason,
                task_id=task_id,
                traceback=str(einfo) if einfo is not None else None,
            )
        except Exception:  # pragma: no cover — never mask the original failure
            logger.exception("Failed to publish DLQ record for task %s", task_id)
        metrics.deliver_dead_lettered_total.labels(method=method, reason=reason).inc()


# ── Generic task ─────────────────────────────────────────────────────────────


@celery_app.task(
    base=_DeliveryTask,
    name="src.tasks.delivery.deliver",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=5,
    rate_limit=None,  # we enforce our own limits in-task
)
def deliver(self, *, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Call a Telegram Bot API ``method`` with ``payload``, rate-limited.

    ``payload`` is serialized as JSON and POSTed to
    ``https://api.telegram.org/bot<token>/<method>``. Any JSON-serializable
    Bot API field is supported (text, captions, ``reply_markup``,
    ``parse_mode``, ``business_connection_id``, media by URL / ``file_id``,
    etc.).

    Uploading raw file bytes (``multipart/form-data``) is out of scope here
    — it's the rare case for bots, and the Right Way is to upload once and
    reuse the returned ``file_id`` from then on.
    """
    chat_id = payload.get("chat_id") if method not in _CHATLESS_METHODS else None
    r = _sync_redis()
    _wait_for_slot(r, chat_id)

    # No PTB instance in the worker process — we talk to Bot API directly
    # over sync HTTP to keep the delivery-worker footprint tiny.
    import httpx

    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"

    with httpx.Client(timeout=10.0) as http:
        resp = http.post(url, json=payload)

    if resp.status_code == 429:
        retry_after = _retry_after_from_429(resp.json() or {})
        logger.warning("Telegram 429 on %s — retry_after=%ss", method, retry_after)
        metrics.deliver_throttled_total.labels(method=method).inc()
        metrics.deliver_retried_total.labels(method=method, reason="throttled").inc()
        raise Retry(when=retry_after)
    if 500 <= resp.status_code < 600:
        logger.error(
            "Telegram API 5xx on %s: %s %s", method, resp.status_code, resp.text
        )
        metrics.deliver_server_error_total.labels(method=method).inc()
        metrics.deliver_retried_total.labels(method=method, reason="server_error").inc()
        # Raise — ``autoretry_for=(Exception,)`` on this task catches it and
        # re-raises as ``Retry`` with exponential backoff. On the final
        # attempt ``MaxRetriesExceededError`` propagates up, triggering
        # ``_DeliveryTask.on_failure`` → DLQ.
        raise RuntimeError(f"Telegram 5xx on {method}: {resp.status_code}")
    if resp.status_code >= 400:
        logger.error(
            "Telegram API error on %s: %s %s", method, resp.status_code, resp.text
        )
        resp.raise_for_status()

    data = resp.json()
    metrics.deliver_sent_total.labels(method=method).inc()
    return data.get("result") if isinstance(data, dict) else None


# ── Facade ───────────────────────────────────────────────────────────────────
#
# Ergonomic helpers on top of ``deliver``. They just assemble a payload and
# enqueue — no rate-limit logic lives here. Add new helpers freely; keep them
# one-liners over ``deliver.delay(...)``.


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so we never send ``"key": null`` to Bot API."""
    return {k: v for k, v in payload.items() if v is not None}


def send_text(
    chat_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_to_message_id: int | None = None,
    reply_markup: dict[str, Any] | None = None,
    business_connection_id: str | None = None,
    disable_notification: bool | None = None,
):
    """Enqueue a ``sendMessage`` call. Returns the Celery ``AsyncResult``."""
    payload = _clean(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_parameters": (
                {"message_id": reply_to_message_id} if reply_to_message_id else None
            ),
            "reply_markup": reply_markup,
            "business_connection_id": business_connection_id,
            "disable_notification": disable_notification,
        }
    )
    return deliver.delay(method="sendMessage", payload=payload)


def send_photo(
    chat_id: int,
    photo: str,
    *,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
    reply_markup: dict[str, Any] | None = None,
    business_connection_id: str | None = None,
):
    """Enqueue a ``sendPhoto`` call. ``photo`` is a URL or ``file_id``."""
    payload = _clean(
        {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode if caption else None,
            "reply_markup": reply_markup,
            "business_connection_id": business_connection_id,
        }
    )
    return deliver.delay(method="sendPhoto", payload=payload)


def edit_text(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    reply_markup: dict[str, Any] | None = None,
):
    """Enqueue an ``editMessageText`` call."""
    payload = _clean(
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
    )
    return deliver.delay(method="editMessageText", payload=payload)


__all__ = ["deliver", "send_text", "send_photo", "edit_text"]
