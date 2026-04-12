"""UUID v7 helpers.

UUID v7 embeds a Unix-millisecond timestamp in the high bits, so values sort
monotonically by insertion time — ideal for database primary keys.
"""

from __future__ import annotations

import uuid as _std_uuid

try:
    # Primary path — fast Rust-backed implementation.
    import uuid_utils
except ImportError:  # pragma: no cover
    uuid_utils = None  # type: ignore[assignment]


def uuid7() -> _std_uuid.UUID:
    """Return a new UUIDv7 as a standard :class:`uuid.UUID`."""
    if uuid_utils is not None:
        # uuid_utils returns its own UUID type; re-wrap into stdlib UUID so
        # downstream code (SQLAlchemy, Pydantic, …) works seamlessly.
        return _std_uuid.UUID(str(uuid_utils.uuid7()))
    # Very small fallback — prefer installing uuid_utils in production.
    import os
    import time

    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (
        (ts_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return _std_uuid.UUID(int=value)


def uuid7_str() -> str:
    """String form of a new UUIDv7 — convenient SQLAlchemy default."""
    return str(uuid7())
