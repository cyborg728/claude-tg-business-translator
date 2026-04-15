"""Celery application shared between bot producer and all workers.

Queue inventory
---------------
* ``tasks_queue``         — heavy in-process work (smoke, future LLM tasks).
* ``delivery_queue``      — rate-limited Telegram Bot API sends.
* ``delivery_dlq``        — terminal-failure DLQ (no consumer).
* ``updates.shard.<i>``   — Phase-3 sharded update dispatch, one Celery
  worker per shard. Declared dynamically from ``UPDATES_SHARDS`` so the
  whole fleet agrees on the set.

Worker-process init
-------------------
Update-consumer workers need a long-lived PTB :class:`Application`. It
is constructed in the ``worker_process_init`` hook below — but *only*
when the worker is configured to consume at least one shard queue.
Delivery and tasks workers skip PTB bootstrap (no PTB, no DB pool, no
Bot HTTP pool) to stay lightweight.
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from kombu import Exchange, Queue

from src.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


def _shard_queues() -> tuple[Queue, ...]:
    """One ``updates.shard.<i>`` queue per ``UPDATES_SHARDS``.

    Declared on every worker so the broker knows about them even before
    the receiver publishes. Phase-2 deployments (empty UPDATES_EXCHANGE)
    still declare them — harmless, nothing publishes there.
    """
    if not _settings.updates_exchange:
        # Phase 2: receiver uses the default direct exchange and
        # UPDATES_QUEUE; no shards yet.
        return ()

    exchange = Exchange(
        _settings.updates_exchange,
        type="x-consistent-hash",
        durable=True,
    )
    return tuple(
        Queue(
            _settings.shard_queue_name(i),
            exchange=exchange,
            # "1" is the weight on the binding — equal weights spread
            # traffic uniformly across the hash ring.
            routing_key="1",
            durable=True,
        )
        for i in range(_settings.updates_shards)
    )


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
# ``processing.smoke``      → tasks_queue
# ``processing.handle_update`` → shard queue (picked by the broker, not us)
# ``delivery.*``           → delivery_queue
# ``delivery_dlq``         → declared only (no consumer; DLQ sink)
celery_app.conf.update(
    task_default_queue=_settings.queue_tasks,
    task_queues=(
        Queue(_settings.queue_tasks),
        Queue(_settings.queue_delivery),
        Queue(_settings.queue_delivery_dlq),
        *_shard_queues(),
    ),
    task_routes={
        "src.tasks.processing.smoke": {"queue": _settings.queue_tasks},
        "src.tasks.delivery.*": {"queue": _settings.queue_delivery},
        # handle_update is *not* routed here — the receiver publishes
        # it directly to the exchange with a chat_id routing key, so
        # Celery's task_routes would get in the way. The consumer
        # specifies its shard via ``-Q updates.shard.<i>``.
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


# ── Update-consumer worker bootstrap ─────────────────────────────────────────
# These hooks fire for *every* Celery worker process, not just
# update-consumers. We gate the expensive PTB bootstrap on the queues the
# worker is actually consuming so a delivery worker stays lightweight.


def _is_update_consumer_process() -> bool:
    """True when this forked worker process will consume a shard queue.

    Celery exposes active queues on the worker only after the
    consumer is attached, but the ``-Q`` CLI flag is in ``sys.argv``
    by the time ``worker_process_init`` runs, so we inspect it there.
    """
    import sys

    argv = " ".join(sys.argv)
    return "updates.shard." in argv


@worker_process_init.connect
def _on_worker_process_init(**_kwargs) -> None:
    if not _is_update_consumer_process():
        return
    # Local import — keeps the PTB / telegram package out of the import
    # graph for delivery / tasks workers.
    from .update_consumer import init_worker_process

    logger.info("Bootstrapping PTB Application for update-consumer process")
    init_worker_process(_settings)


@worker_process_shutdown.connect
def _on_worker_process_shutdown(**_kwargs) -> None:
    if not _is_update_consumer_process():
        return
    from .update_consumer import shutdown_worker_process

    shutdown_worker_process()


__all__ = ["celery_app"]
