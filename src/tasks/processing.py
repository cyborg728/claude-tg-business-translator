"""Heavy-work tasks (consumed by the *tasks_queue* worker).

The producer is the bot (e.g. ``/smoke`` handler). The worker executes
the slow work, then enqueues the result to the delivery queue.

Phase 3 also adds ``handle_update`` here — it's consumed off shard
queues by the update-consumer worker fleet; the PTB Application is
built once per worker process (see :mod:`src.tasks.update_consumer`).
"""

from __future__ import annotations

import logging
import time

from src.config import get_settings

from .celery_app import celery_app
from .delivery import send_text
from .metrics import handler_duration_seconds

logger = logging.getLogger(__name__)


@celery_app.task(
    name="src.tasks.processing.smoke",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def smoke(self, chat_id: int, locale: str, delay_s: int = 5) -> None:
    """Smoke-test the full pipeline: slow job → delivery queue → Telegram.

    Kept around as a sanity check for the end-to-end wiring of both
    workers. It gets deleted (or repurposed) as soon as the first real
    feature lands.
    """
    from src.i18n import get_translator

    logger.info(
        "[processing.smoke] chat_id=%s locale=%s delay=%ss", chat_id, locale, delay_s
    )
    # Blocking sleep is fine: Celery workers are sync by default, and each
    # worker process occupies a single concurrency slot while it sleeps.
    time.sleep(delay_s)

    text = get_translator().gettext("smoke-success", locale=locale)
    send_text(chat_id, text)


@celery_app.task(
    name="src.tasks.processing.handle_update",
    bind=True,
    # Update-dispatch failures are usually transient (DB hiccup, Redis
    # blip). Back off and retry a few times before letting the message
    # go to the shard's DLX.
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
    max_retries=3,
    # Crucial for ordering: ack only after successful dispatch. Paired
    # with ``worker_concurrency=1`` + ``worker_prefetch_multiplier=1``,
    # this guarantees no out-of-order dispatch per shard.
    acks_late=True,
)
def handle_update(self, raw_update: dict) -> None:
    """Dispatch one Telegram update through the shared PTB Application.

    Called from the update-consumer worker fleet. The raw dict is the
    exact JSON body Telegram posted to the receiver — no mutation on the
    publish path, so this task is a faithful replay.
    """
    # Imported lazily so non-consumer workers (delivery, tasks) don't
    # pay the PTB import cost.
    from .update_consumer import dispatch_update

    update_id = raw_update.get("update_id")
    logger.debug("[processing.handle_update] update_id=%s", update_id)

    delivery_info = getattr(self.request, "delivery_info", None) or {}
    shard = delivery_info.get("routing_key", "unknown")
    with handler_duration_seconds.labels(shard=shard).time():
        dispatch_update(raw_update)
