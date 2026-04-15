"""Single source of truth for runtime configuration.

All settings are read from environment variables or a ``.env`` file.
See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(..., description="Bot token from @BotFather")

    # ── Bot mode ──────────────────────────────────────────────────────────────
    mode: Literal["polling", "webhook", "receiver"] = Field(
        "polling", description="polling|webhook|receiver"
    )

    # Webhook (required when mode=webhook)
    webhook_base_url: str = Field("https://example.f8f.dev", description="Public base URL")
    webhook_port: int = Field(8080, description="Local port the bot listens on")
    webhook_secret_token: str = Field("", description="Secret token for webhook security")

    # ── i18n ──────────────────────────────────────────────────────────────────
    default_locale: str = Field("en", description="ISO-639-1 fallback locale")

    # ── Database ──────────────────────────────────────────────────────────────
    database_backend: Literal["sqlite"] = Field("sqlite", description="Database backend")
    database_path: str = Field("data/bot.db", description="SQLite file path")

    # ── RabbitMQ / Celery ─────────────────────────────────────────────────────
    rabbitmq_url: str = Field("amqp://guest:guest@localhost:5672//")
    queue_tasks: str = Field("tasks_queue")
    queue_delivery: str = Field("delivery_queue")
    queue_delivery_dlq: str = Field(
        "delivery_dlq",
        description="Dead-letter queue for delivery tasks that exhausted retries",
    )

    # ── Incoming-updates transport (webhook-receiver → RabbitMQ) ─────────────
    # Phase 2 baseline: ``updates_exchange=""`` → default direct exchange →
    # single ``updates_queue``.
    # Phase 3 production: ``updates_exchange="updates"`` (type
    # ``x-consistent-hash``) → N queues ``updates.shard.<i>``, each bound
    # with weight ``1``. Routing key is ``str(chat_id)``.
    updates_exchange: str = Field(
        "",
        description=(
            "RabbitMQ exchange the receiver publishes updates to. Empty "
            "means the built-in default direct exchange (Phase 2); set to "
            "a non-empty name (e.g. 'updates') to switch to the Phase-3 "
            "x-consistent-hash topology."
        ),
    )
    updates_queue: str = Field(
        "updates_queue",
        description=(
            "Single-queue target for Phase 2. Ignored when UPDATES_EXCHANGE "
            "is set — Phase 3 fans out to UPDATES_SHARDS shard queues."
        ),
    )
    updates_shards: int = Field(
        16,
        ge=1,
        le=256,
        description=(
            "Phase 3: number of shard queues bound to the consistent-hash "
            "exchange. Shard queue names follow "
            "'updates.shard.0' .. 'updates.shard.<N-1>'. Changing this at "
            "runtime requires a coordinated re-declaration across the fleet."
        ),
    )

    def shard_queue_name(self, index: int) -> str:
        """Canonical name of the i-th update shard queue."""
        if not 0 <= index < self.updates_shards:
            raise ValueError(
                f"shard index {index} out of range [0, {self.updates_shards})"
            )
        return f"updates.shard.{index}"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field("redis://localhost:6379/0")
    redis_save_ttl: int = Field(3600, description="/redis_save TTL in seconds; 0=forever")

    # ── Delivery rate-limit ───────────────────────────────────────────────────
    delivery_rate_per_second: int = Field(25, ge=1, description="Global messages per second")
    delivery_rate_per_chat: int = Field(1, ge=1, description="Per-chat messages per second")

    # ── Idempotency ───────────────────────────────────────────────────────────
    dedup_ttl_seconds: int = Field(
        3600,
        ge=1,
        description="How long an update_id stays claimed in Redis (seconds)",
    )

    # ── Derived ───────────────────────────────────────────────────────────────
    @property
    def database_url(self) -> str:
        if self.database_backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.database_path}"
        raise RuntimeError(f"Unsupported DATABASE_BACKEND={self.database_backend}")

    @property
    def database_url_sync(self) -> str:
        """Sync URL — used by Alembic migrations."""
        if self.database_backend == "sqlite":
            return f"sqlite:///{self.database_path}"
        raise RuntimeError(f"Unsupported DATABASE_BACKEND={self.database_backend}")

    @property
    def webhook_path(self) -> str:
        return f"/{self.telegram_bot_token}"

    @property
    def webhook_full_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def celery_broker_url(self) -> str:
        return self.rabbitmq_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("webhook_base_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v

    @field_validator("mode", "database_backend", mode="before")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower().strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _require_webhook_url_in_webhook_mode(self) -> "Settings":
        if self.mode in ("webhook", "receiver") and not self.webhook_base_url:
            raise ValueError("WEBHOOK_BASE_URL must be set when MODE=webhook|receiver")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
