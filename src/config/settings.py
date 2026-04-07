from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(..., description="Telegram bot token from @BotFather")
    owner_chat_id: int = Field(..., description="Owner Telegram chat ID for receiving notifications")

    # ── Gemini ────────────────────────────────────────────────────────────────
    gemini_api_key: str = Field(..., description="Google Gemini API key")
    gemini_model: str = Field("gemini-2.0-flash", description="Gemini model name")

    # ── Translation ───────────────────────────────────────────────────────────
    owner_language: str = Field("ru", description="ISO 639-1 language code the owner writes in")

    # ── Bot mode ──────────────────────────────────────────────────────────────
    mode: Literal["polling", "webhook"] = Field("polling", description="polling or webhook")

    # Webhook (required when mode=webhook)
    webhook_url: str = Field("", description="Public base URL, e.g. https://example.com")
    webhook_port: int = Field(8080, description="Local port the bot listens on")
    webhook_secret_token: str = Field("", description="Secret token for webhook security")

    # ── Database ──────────────────────────────────────────────────────────────
    database_path: str = Field("data/bot.db", description="Path to SQLite database file")

    # ── Interface locale ──────────────────────────────────────────────────────
    locale: Literal["en", "ru"] = Field("en", description="Bot interface language: en or ru")

    # ── Derived properties ────────────────────────────────────────────────────
    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"

    @property
    def webhook_path(self) -> str:
        return f"/{self.telegram_bot_token}"

    @property
    def webhook_full_url(self) -> str:
        return f"{self.webhook_url.rstrip('/')}{self.webhook_path}"

    @field_validator("webhook_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v: str) -> str:
        return v.lower().strip()

    @model_validator(mode="after")
    def _require_webhook_url_in_webhook_mode(self) -> "Settings":
        if self.mode == "webhook" and not self.webhook_url:
            raise ValueError("webhook_url must be set when mode='webhook'")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
