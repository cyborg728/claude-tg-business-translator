"""UUIDv7 generator: shape, version bits, monotonicity, fallback path."""

from __future__ import annotations

import uuid

import pytest

from src.utils.ids import uuid7, uuid7_str


def test_uuid7_returns_uuid_object():
    value = uuid7()
    assert isinstance(value, uuid.UUID)


def test_uuid7_version_field_is_7():
    # RFC 9562 §5.7: bits 48-51 of UUIDv7 must equal 0b0111.
    for _ in range(50):
        assert uuid7().version == 7


def test_uuid7_variant_is_rfc4122():
    # Two top bits of clock_seq_hi_and_reserved must be 10.
    for _ in range(20):
        assert uuid7().variant == uuid.RFC_4122


def test_uuid7_monotonically_increasing():
    """Embedded ms-timestamp keeps newly generated UUIDs sorted."""
    values = [uuid7() for _ in range(2_000)]
    timestamps = [v.int >> 80 for v in values]
    # Allow non-strict (==) within the same millisecond, but never decreasing.
    assert all(b >= a for a, b in zip(timestamps, timestamps[1:]))


def test_uuid7_str_roundtrips():
    s = uuid7_str()
    assert isinstance(s, str)
    parsed = uuid.UUID(s)
    assert parsed.version == 7


def test_uuid7_uniqueness():
    values = {uuid7() for _ in range(5_000)}
    assert len(values) == 5_000


def test_uuid7_fallback_path(monkeypatch: pytest.MonkeyPatch):
    """Force the no-uuid_utils branch and check we still produce v7."""
    import src.utils.ids as ids

    monkeypatch.setattr(ids, "uuid_utils", None)
    value = ids.uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122
