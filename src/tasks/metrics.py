"""Prometheus counters for the delivery worker and dedup layer.

Counters live at module import time — they register themselves with the
process-wide default ``CollectorRegistry``. There is no HTTP endpoint
yet (Phase 5 wires one via the receiver / a ``/metrics`` sidecar); tests
assert the counters tick by reading ``.labels(...)._value.get()``.

Naming follows Prometheus conventions: ``<subsystem>_<thing>_total`` for
counters, lowercase, plural.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ── Delivery (``src.tasks.delivery.deliver``) ────────────────────────────────
deliver_sent_total = Counter(
    "deliver_sent_total",
    "Successful Telegram Bot API calls.",
    labelnames=("method",),
)
deliver_throttled_total = Counter(
    "deliver_throttled_total",
    "Bot API 429 responses (rate-limit).",
    labelnames=("method",),
)
deliver_server_error_total = Counter(
    "deliver_server_error_total",
    "Bot API 5xx responses.",
    labelnames=("method",),
)
deliver_retried_total = Counter(
    "deliver_retried_total",
    "Task retry attempts, by reason.",
    labelnames=("method", "reason"),
)
deliver_dead_lettered_total = Counter(
    "deliver_dead_lettered_total",
    "Tasks routed to the delivery DLQ after exhausting retries.",
    labelnames=("method", "reason"),
)

# ── Idempotency (``src.cache.idempotency``) ──────────────────────────────────
dedup_hit_total = Counter(
    "dedup_hit_total",
    "Updates dropped because the update_id was already claimed.",
)
dedup_miss_total = Counter(
    "dedup_miss_total",
    "First-time updates accepted by the dedup layer.",
)


# ── Webhook receiver (``src.receiver.app``) ─────────────────────────────────
receiver_requests_total = Counter(
    "receiver_requests_total",
    "Inbound webhook requests by outcome.",
    labelnames=("outcome",),
)
receiver_publish_duration_seconds = Histogram(
    "receiver_publish_duration_seconds",
    "Time spent publishing an update to RabbitMQ.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# ── Handler latency (``src.tasks.handle_update``) ───────────────────────────
handler_duration_seconds = Histogram(
    "handler_duration_seconds",
    "Wall-clock time of handle_update Celery task execution.",
    labelnames=("shard",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


__all__ = [
    "dedup_hit_total",
    "dedup_miss_total",
    "deliver_dead_lettered_total",
    "deliver_retried_total",
    "deliver_sent_total",
    "deliver_server_error_total",
    "deliver_throttled_total",
    "handler_duration_seconds",
    "receiver_publish_duration_seconds",
    "receiver_requests_total",
]
