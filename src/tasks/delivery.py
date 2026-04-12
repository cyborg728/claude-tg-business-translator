"""Rate-limited Telegram delivery worker (consumes *delivery_queue*).

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
"""

from __future__ import annotations

import logging
import time

from celery.exceptions import Retry

from src.config import get_settings

from .celery_app import celery_app

logger = logging.getLogger(__name__)

_GLOBAL_KEY = "rl:global"
_CHAT_KEY_PREFIX = "rl:chat:"


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


def _wait_for_slot(r, chat_id: int) -> None:
    settings = get_settings()
    max_wait_s = 5.0
    elapsed = 0.0
    sleep_step = 0.05

    while elapsed < max_wait_s:
        global_ok = _try_acquire(r, _GLOBAL_KEY, settings.delivery_rate_per_second)
        chat_ok = _try_acquire(
            r, f"{_CHAT_KEY_PREFIX}{chat_id}", settings.delivery_rate_per_chat
        )
        if global_ok and chat_ok:
            return
        time.sleep(sleep_step)
        elapsed += sleep_step
    # Reached max-wait — let Celery retry with backoff.
    raise Retry(when=1)


@celery_app.task(
    name="src.tasks.delivery.deliver_message",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=5,
    rate_limit=None,  # we enforce our own limits in-task
)
def deliver_message(
    self,
    *,
    chat_id: int,
    text: str,
    parse_mode: str | None = "HTML",
    reply_to_message_id: int | None = None,
    business_connection_id: str | None = None,
) -> None:
    """Send a text message to ``chat_id`` obeying our rate-limit budget."""
    r = _sync_redis()
    _wait_for_slot(r, chat_id)

    # Use Telegram Bot API directly (sync HTTP) — no PTB instance in the
    # worker process, so we keep the dependency surface small.
    import httpx

    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    if business_connection_id:
        payload["business_connection_id"] = business_connection_id

    with httpx.Client(timeout=10.0) as http:
        resp = http.post(url, json=payload)
    if resp.status_code == 429:
        retry_after = int(resp.json().get("parameters", {}).get("retry_after", 1))
        logger.warning("Telegram 429 — retry_after=%ss", retry_after)
        raise Retry(when=retry_after)
    if resp.status_code >= 400:
        logger.error("Telegram API error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()
