from __future__ import annotations

from prometheus_client import Counter, Histogram

# ── Dedup ────────────────────────────────────────────────────────────────────
dedup_hit_total = Counter(
    "dedup_hit_total",
    "Updates dropped because the update_id was already claimed.",
)
dedup_miss_total = Counter(
    "dedup_miss_total",
    "First-time updates accepted by the dedup layer.",
)

# ── Receiver ─────────────────────────────────────────────────────────────────
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

__all__ = [
    "dedup_hit_total",
    "dedup_miss_total",
    "receiver_publish_duration_seconds",
    "receiver_requests_total",
]
