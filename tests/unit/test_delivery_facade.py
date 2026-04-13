"""Unit tests for the delivery facade (``send_text`` / ``send_photo`` / ``edit_text``).

These verify the helpers assemble the right Bot API payload and dispatch
via ``deliver.delay``. No Redis, no HTTP — those live in
``tests/integration/test_delivery_task.py``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tasks import delivery


@pytest.fixture
def fake_delay():
    with patch.object(delivery.deliver, "delay") as m:
        yield m


def test_clean_drops_none_keeps_falsy():
    out = delivery._clean(
        {"a": 1, "b": None, "c": "", "d": 0, "e": False, "f": {}, "g": None}
    )
    assert out == {"a": 1, "c": "", "d": 0, "e": False, "f": {}}


def test_send_text_minimal(fake_delay):
    delivery.send_text(42, "hi")
    fake_delay.assert_called_once_with(
        method="sendMessage",
        payload={"chat_id": 42, "text": "hi", "parse_mode": "HTML"},
    )


def test_send_text_full(fake_delay):
    delivery.send_text(
        42,
        "hi",
        parse_mode="MarkdownV2",
        reply_to_message_id=7,
        reply_markup={"inline_keyboard": [[{"text": "ok", "callback_data": "x"}]]},
        business_connection_id="bc-1",
        disable_notification=True,
    )
    fake_delay.assert_called_once_with(
        method="sendMessage",
        payload={
            "chat_id": 42,
            "text": "hi",
            "parse_mode": "MarkdownV2",
            "reply_parameters": {"message_id": 7},
            "reply_markup": {"inline_keyboard": [[{"text": "ok", "callback_data": "x"}]]},
            "business_connection_id": "bc-1",
            "disable_notification": True,
        },
    )


def test_send_text_parse_mode_none_is_dropped(fake_delay):
    delivery.send_text(42, "hi", parse_mode=None)
    assert "parse_mode" not in fake_delay.call_args.kwargs["payload"]


def test_send_photo_with_caption(fake_delay):
    delivery.send_photo(42, "https://img/cat.jpg", caption="a <b>cat</b>")
    fake_delay.assert_called_once_with(
        method="sendPhoto",
        payload={
            "chat_id": 42,
            "photo": "https://img/cat.jpg",
            "caption": "a <b>cat</b>",
            "parse_mode": "HTML",
        },
    )


def test_send_photo_without_caption_skips_parse_mode(fake_delay):
    delivery.send_photo(42, "AgADBAADq... (file_id)")
    payload = fake_delay.call_args.kwargs["payload"]
    assert "caption" not in payload
    assert "parse_mode" not in payload  # parse_mode without caption makes no sense


def test_send_photo_with_buttons(fake_delay):
    markup = {"inline_keyboard": [[{"text": "buy", "callback_data": "buy:1"}]]}
    delivery.send_photo(42, "file_id_xyz", reply_markup=markup)
    assert fake_delay.call_args.kwargs["payload"]["reply_markup"] == markup


def test_edit_text(fake_delay):
    delivery.edit_text(42, 100, "new")
    fake_delay.assert_called_once_with(
        method="editMessageText",
        payload={"chat_id": 42, "message_id": 100, "text": "new", "parse_mode": "HTML"},
    )
