from __future__ import annotations

from typing import Any

import pytest

from src.receiver.chat_id import extract_chat_id


@pytest.mark.parametrize(
    "update, expected",
    [
        ({"message": {"chat": {"id": 10}}}, 10),
        ({"edited_message": {"chat": {"id": 11}}}, 11),
        ({"channel_post": {"chat": {"id": 12}}}, 12),
        ({"business_message": {"chat": {"id": 13}}}, 13),
        ({"callback_query": {"message": {"chat": {"id": 14}}}}, 14),
        ({"callback_query": {"from": {"id": 15}}}, 15),
        ({"business_connection": {"user_chat_id": 16}}, 16),
        ({"inline_query": {"from": {"id": 17}}}, 17),
        ({"my_chat_member": {"chat": {"id": 18}}}, 18),
        ({"poll": {"id": "x"}}, None),
        ({}, None),
    ],
)
def test_extract_chat_id(update: dict[str, Any], expected: int | None) -> None:
    assert extract_chat_id(update) == expected
