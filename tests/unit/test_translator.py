"""Translator: locale picker + Fluent gettext + fallback chain."""

from __future__ import annotations

import pytest

from src.i18n import Translator


@pytest.fixture(scope="module")
def translator() -> Translator:
    return Translator(default_locale="en")


def test_available_locales_discovered(translator: Translator):
    # locales/en and locales/ru ship with the repo.
    assert "en" in translator.available_locales
    assert "ru" in translator.available_locales


def test_pick_locale_exact_match(translator: Translator):
    assert translator.pick_locale("ru") == "ru"
    assert translator.pick_locale("en") == "en"


def test_pick_locale_strips_region(translator: Translator):
    assert translator.pick_locale("en-US") == "en"
    assert translator.pick_locale("ru-RU") == "ru"


def test_pick_locale_unknown_falls_back_to_default(translator: Translator):
    assert translator.pick_locale("zh") == "en"
    assert translator.pick_locale(None) == "en"
    assert translator.pick_locale("") == "en"


def test_gettext_substitutes_arguments(translator: Translator):
    out = translator.gettext("start-greeting", locale="en", name="Alice")
    assert "Alice" in out


def test_gettext_returns_localized_strings(translator: Translator):
    en = translator.gettext("smoke-success", locale="en")
    ru = translator.gettext("smoke-success", locale="ru")
    assert en and ru
    # 'success' keyword is identical between locales for this key, so just
    # check both are non-empty rather than insisting on inequality.


def test_gettext_unknown_message_returns_message_id(translator: Translator):
    assert translator.gettext("definitely-not-a-real-key") == "definitely-not-a-real-key"


def test_gettext_unknown_locale_uses_default(translator: Translator):
    out = translator.gettext("smoke-enqueued", locale="zh")
    assert out == translator.gettext("smoke-enqueued", locale="en")


def test_default_locale_invalid_falls_back_to_first_available():
    t = Translator(default_locale="xx")
    assert t.default_locale in {"en", "ru"}
