from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(..., description="Bot token from @BotFather")
    telegram_api_base_url: str = Field("https://api.telegram.org")

    @property
    def telegram_api_url(self) -> str:
        return f"{self.telegram_api_base_url.rstrip('/')}/bot{self.telegram_bot_token}"

    # ── Webhook ──────────────────────────────────────────────────────────
    webhook_base_url: str = Field("https://example.f8f.dev")
    webhook_port: int = Field(8080)
    webhook_secret_token: str = Field("")

    # ── RabbitMQ ─────────────────────────────────────────────────────────
    rabbitmq_url: str = Field("amqp://guest:guest@localhost:5672//")
    updates_exchange: str = Field(
        "",
        description=(
            "Empty = default direct exchange (single queue); "
            "non-empty = x-consistent-hash topology."
        ),
    )
    updates_queue: str = Field("updates_queue")
    updates_shards: int = Field(16, ge=1, le=256)

    def shard_queue_name(self, index: int) -> str:
        if not 0 <= index < self.updates_shards:
            raise ValueError(
                f"shard index {index} out of range [0, {self.updates_shards})"
            )
        return f"updates.shard.{index}"

    # ── Redis ────────────────────────────────────────────────────────────
    redis_url: str = Field("redis://localhost:6379/0")

    # ── Dedup ────────────────────────────────────────────────────────────
    dedup_ttl_seconds: int = Field(3600, ge=1)

    # ── Derived ──────────────────────────────────────────────────────────
    @property
    def webhook_path(self) -> str:
        return f"/{self.telegram_bot_token}"

    @property
    def webhook_full_url(self) -> str:
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"

    # ── Validators ───────────────────────────────────────────────────────
    @field_validator("webhook_base_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v

    @model_validator(mode="after")
    def _require_webhook_url(self) -> Settings:
        if not self.webhook_base_url:
            raise ValueError("WEBHOOK_BASE_URL must be set")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
