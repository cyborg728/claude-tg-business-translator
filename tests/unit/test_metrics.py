"""Unit tests for ``src/tasks/metrics.py`` — metric declarations."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from src.tasks.metrics import (
    dedup_hit_total,
    dedup_miss_total,
    deliver_dead_lettered_total,
    deliver_retried_total,
    deliver_sent_total,
    deliver_server_error_total,
    deliver_throttled_total,
    handler_duration_seconds,
    receiver_publish_duration_seconds,
    receiver_requests_total,
)


def test_all_counters_are_counter_type():
    for metric in [
        deliver_sent_total,
        deliver_throttled_total,
        deliver_server_error_total,
        deliver_retried_total,
        deliver_dead_lettered_total,
        dedup_hit_total,
        dedup_miss_total,
        receiver_requests_total,
    ]:
        assert isinstance(metric, Counter), f"{metric._name} is not a Counter"


def test_all_histograms_are_histogram_type():
    for metric in [
        receiver_publish_duration_seconds,
        handler_duration_seconds,
    ]:
        assert isinstance(metric, Histogram), f"{metric._name} is not a Histogram"


def test_delivery_counters_have_method_label():
    for metric in [
        deliver_sent_total,
        deliver_throttled_total,
        deliver_server_error_total,
    ]:
        assert "method" in metric._labelnames, metric._name


def test_receiver_requests_has_outcome_label():
    assert "outcome" in receiver_requests_total._labelnames


def test_handler_duration_has_shard_label():
    assert "shard" in handler_duration_seconds._labelnames


def test_receiver_publish_duration_has_custom_buckets():
    assert len(receiver_publish_duration_seconds._upper_bounds) > 5
    assert receiver_publish_duration_seconds._upper_bounds[0] == 0.001
