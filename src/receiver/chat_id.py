"""Extract ``chat_id`` from a raw Telegram Update JSON.

Used by the receiver to decide which shard an update belongs to. The
value is attached to the broker message so Phase 3's consistent-hash
routing can work without parsing the body again.

Some update types have no chat — inline queries, business-connection
events — those fall back to the sender/user id or ``None`` so the
publisher can decide what to do (Phase 2: everything lands in one
queue; Phase 3: chat-less updates bypass the ordered shards).
"""

from __future__ import annotations

from typing import Any

# Update keys whose value is a Message with a ``chat`` object.
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
    """Best-effort chat id. Returns ``None`` if the update has no chat."""
    for key in _MESSAGE_LIKE_KEYS:
        obj = update.get(key)
        if isinstance(obj, dict):
            chat = obj.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return chat["id"]

    # callback_query has a nested ``message.chat``; fall back to from_.id
    # for inline-keyboard callbacks without an anchor message.
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

    # business_connection carries ``user_chat_id`` (the user's private chat).
    bc = update.get("business_connection")
    if isinstance(bc, dict) and "user_chat_id" in bc:
        return bc["user_chat_id"]

    # inline_query / chosen_inline_result / pre_checkout_query / shipping_query
    # all have ``from`` with a user id but no chat — use it as the routing key
    # so the same user's pipeline stays consistent.
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
