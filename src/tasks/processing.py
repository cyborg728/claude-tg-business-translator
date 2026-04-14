"""Heavy-work tasks (consumed by the *tasks_queue* worker).

The producer is the bot (e.g. ``/smoke`` handler). The worker executes
the slow work, then enqueues the result to the delivery queue.
"""

from __future__ import annotations

import logging
import time

from .celery_app import celery_app
from .delivery import send_text

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
