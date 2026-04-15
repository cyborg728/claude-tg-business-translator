"""Celery application shared between bot producer and all workers."""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from src.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "tg_business_bot",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=[
        "src.tasks.processing",
        "src.tasks.delivery",
    ],
)

# ── Routing ──────────────────────────────────────────────────────────────────
# ``processing.*`` tasks go to the heavy-work queue.
# ``delivery.*``   tasks go to the rate-limited Telegram-sending queue.
# ``delivery_dlq`` is declared here so the broker has it on worker startup;
# it has **no consumer** — terminal failures from ``deliver`` publish raw
# records there for operators to inspect / replay manually.
celery_app.conf.update(
    task_default_queue=_settings.queue_tasks,
    task_queues=(
        Queue(_settings.queue_tasks),
        Queue(_settings.queue_delivery),
        Queue(_settings.queue_delivery_dlq),
    ),
    task_routes={
        "src.tasks.processing.*": {"queue": _settings.queue_tasks},
        "src.tasks.delivery.*": {"queue": _settings.queue_delivery},
    },
    # Reliability / behavior
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
)
