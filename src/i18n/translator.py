import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).parent / "locales"
_SUPPORTED_LOCALES = {"en", "ru"}
_FALLBACK_LOCALE = "en"


class Translator:
    """Simple key→string translator backed by JSON locale files.

    Usage::

        t = Translator("ru")
        text = t("new_user_message", name="Ivan", original="Hello", translation="Привет")
    """

    def __init__(self, locale: str) -> None:
        resolved = locale if locale in _SUPPORTED_LOCALES else _FALLBACK_LOCALE
        if resolved != locale:
            logger.warning("Locale %r not supported — falling back to %r", locale, resolved)
        self._locale = resolved
        self._messages: dict[str, str] = self._load(resolved)

    # ── Public API ────────────────────────────────────────────────────────────

    def __call__(self, key: str, **kwargs: object) -> str:
        return self.get(key, **kwargs)

    def get(self, key: str, **kwargs: object) -> str:
        """Return the translated string for *key*, formatted with *kwargs*."""
        template = self._messages.get(key)
        if template is None:
            logger.error("Missing translation key %r for locale %r", key, self._locale)
            template = self._fallback(key)
        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError as exc:
                logger.error(
                    "Translation format error for key %r: missing placeholder %s", key, exc
                )
        return template

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _load(locale: str) -> dict[str, str]:
        path = _LOCALES_DIR / f"{locale}.json"
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def switch_locale(self, locale: str) -> None:
        """Reload messages for a different locale in-place.

        All handlers that share this Translator instance will immediately
        start using the new locale.
        """
        resolved = locale if locale in _SUPPORTED_LOCALES else _FALLBACK_LOCALE
        if resolved != locale:
            logger.warning("Locale %r not supported — falling back to %r", locale, resolved)
        self._locale = resolved
        self._messages = self._load(resolved)

    def _fallback(self, key: str) -> str:
        """Try the English fallback file, then return the raw key."""
        if self._locale != _FALLBACK_LOCALE:
            fallback_messages = self._load(_FALLBACK_LOCALE)
            if key in fallback_messages:
                return fallback_messages[key]
        return key
