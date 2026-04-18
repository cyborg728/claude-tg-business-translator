from __future__ import annotations

from typing import Any

_MESSAGE_LIKE_KEYS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "business_message",
    "edited_business_message",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
    "message_reaction",
    "message_reaction_count",
)


def extract_chat_id(update: dict[str, Any]) -> int | None:
    for key in _MESSAGE_LIKE_KEYS:
        obj = update.get(key)
        if isinstance(obj, dict):
            chat = obj.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return chat["id"]

    cbq = update.get("callback_query")
    if isinstance(cbq, dict):
        msg = cbq.get("message") or {}
        if isinstance(msg, dict):
            chat = msg.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return chat["id"]
        from_ = cbq.get("from") or {}
        if isinstance(from_, dict) and "id" in from_:
            return from_["id"]

    bc = update.get("business_connection")
    if isinstance(bc, dict) and "user_chat_id" in bc:
        return bc["user_chat_id"]

    for key in (
        "inline_query",
        "chosen_inline_result",
        "pre_checkout_query",
        "shipping_query",
    ):
        obj = update.get(key)
        if isinstance(obj, dict):
            from_ = obj.get("from") or {}
            if isinstance(from_, dict) and "id" in from_:
                return from_["id"]

    return None


__all__ = ["extract_chat_id"]
