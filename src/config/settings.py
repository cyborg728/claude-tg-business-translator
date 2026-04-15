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

    # ── Incoming-updates transport (Phase 2: webhook-receiver → RabbitMQ) ────
    updates_exchange: str = Field(
        "",
        description=(
            "RabbitMQ exchange the receiver publishes updates to. Default "
            "(empty) means the built-in default direct exchange; Phase 3 "
            "switches this to an 'x-consistent-hash' exchange."
        ),
    )
    updates_queue: str = Field(
        "updates_queue",
        description=(
            "Queue the receiver publishes to on Phase 2 (single-shard). "
            "Phase 3 replaces this with N 'updates.shard.<i>' queues."
        ),
    )

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
