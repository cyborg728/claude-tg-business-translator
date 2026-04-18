from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_START_TEXT = (
    "Привет! Я бот-переводчик для Telegram Business.\n\n"
    "Отправьте /help, чтобы увидеть список команд."
)

_HELP_TEXT = (
    "Доступные команды:\n"
    "/start — приветствие\n"
    "/help — эта справка"
)


class Bot:
    def __init__(self, api_url: str, *, timeout: float = 10.0) -> None:
        self._api_url = api_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or update.get("business_message")
        if not isinstance(message, dict):
            return

        chat = message.get("chat") or {}
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        text = message.get("text")
        if not isinstance(chat_id, int) or not isinstance(text, str):
            return

        command = _parse_command(text)
        business_connection_id = update.get("business_connection_id") or message.get(
            "business_connection_id"
        )

        if command == "/start":
            await self._send(chat_id, _START_TEXT, business_connection_id)
        elif command == "/help":
            await self._send(chat_id, _HELP_TEXT, business_connection_id)

    async def _send(
        self,
        chat_id: int,
        text: str,
        business_connection_id: str | None,
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if isinstance(business_connection_id, str):
            payload["business_connection_id"] = business_connection_id

        resp = await self._client.post(f"{self._api_url}/sendMessage", json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "sendMessage failed chat_id=%s status=%s body=%s",
                chat_id,
                resp.status_code,
                resp.text[:200],
            )


def _parse_command(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    head = text.split(maxsplit=1)[0]
    return head.split("@", 1)[0].lower()


__all__ = ["Bot"]
