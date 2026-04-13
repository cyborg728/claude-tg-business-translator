"""Heavy-work tasks (consumed by the *tasks_queue* worker).

The producer is the bot (e.g. ``/test_queue`` handler). The worker executes
the slow work, then enqueues the result to the delivery queue.
"""

from __future__ import annotations

import logging
import time

from .celery_app import celery_app
from .delivery import send_text

logger = logging.getLogger(__name__)


@celery_app.task(
    name="src.tasks.processing.test_queue",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def test_queue(self, chat_id: int, locale: str, delay_s: int = 5) -> None:
    """Simulate a slow job, then hand off the success message to delivery."""
    from src.i18n import get_translator

    logger.info(
        "[processing.test_queue] chat_id=%s locale=%s delay=%ss", chat_id, locale, delay_s
    )
    # Blocking sleep is fine: Celery workers are sync by default, and each
    # worker process occupies a single concurrency slot while it sleeps.
    time.sleep(delay_s)

    text = get_translator().gettext("queue-success", locale=locale)
    send_text(chat_id, text)
