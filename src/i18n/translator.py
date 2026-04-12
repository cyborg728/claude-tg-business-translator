"""Per-user i18n powered by Fluent (``fluent.runtime``).

Locales are auto-discovered from ``src/i18n/locales/<locale>/*.ftl`` at import
time.  The translator picks the locale per call (e.g. the user's
``language_code`` from Telegram) and falls back to ``default_locale`` whenever
the requested locale is unknown or the message is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fluent.runtime import FluentLocalization, FluentResourceLoader

logger = logging.getLogger(__name__)

_LOCALES_ROOT = Path(__file__).parent / "locales"
_RESOURCE_IDS = ["main.ftl"]


def _discover_locales() -> list[str]:
    if not _LOCALES_ROOT.exists():
        return []
    return sorted(p.name for p in _LOCALES_ROOT.iterdir() if p.is_dir())


class Translator:
    """Stateless multi-locale translator."""

    def __init__(self, default_locale: str = "en") -> None:
        self._available = _discover_locales()
        if not self._available:
            raise RuntimeError(
                f"No locale bundles found under {_LOCALES_ROOT}. "
                "Create at least one directory like locales/en/main.ftl."
            )
        if default_locale not in self._available:
            logger.warning(
                "Default locale %r not found — falling back to %r",
                default_locale,
                self._available[0],
            )
            default_locale = self._available[0]
        self._default_locale = default_locale

        self._loader = FluentResourceLoader(str(_LOCALES_ROOT / "{locale}"))
        # Pre-build one bundle per locale (each with fallback to default).
        self._bundles: dict[str, FluentLocalization] = {}
        for locale in self._available:
            chain = [locale] if locale == default_locale else [locale, default_locale]
            self._bundles[locale] = FluentLocalization(
                locales=chain,
                resource_ids=_RESOURCE_IDS,
                resource_loader=self._loader,
            )

    # ── Public API ────────────────────────────────────────────────────────────
    @property
    def default_locale(self) -> str:
        return self._default_locale

    @property
    def available_locales(self) -> list[str]:
        return list(self._available)

    def pick_locale(self, language_code: str | None) -> str:
        """Return a supported locale that best matches ``language_code``.

        Telegram returns IETF-ish codes like ``ru``, ``en-US``, ``pt-BR`` — we
        accept both exact matches and the primary subtag (``en-US`` → ``en``).
        """
        if not language_code:
            return self._default_locale
        code = language_code.lower()
        if code in self._available:
            return code
        primary = code.split("-", 1)[0]
        if primary in self._available:
            return primary
        return self._default_locale

    def gettext(
        self,
        message_id: str,
        locale: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Return a localised message.

        Falls back to the default locale, then to ``message_id`` itself.
        """
        target = locale if locale in self._bundles else self._default_locale
        bundle = self._bundles[target]
        value = bundle.format_value(message_id, kwargs or None)
        if value == message_id:
            logger.debug("i18n miss: %s/%s", target, message_id)
        return value

    # Convenient alias used throughout the bot handlers.
    t = gettext


# ── Module-level singleton ────────────────────────────────────────────────────
_singleton: Translator | None = None


def get_translator(default_locale: str | None = None) -> Translator:
    """Process-wide translator instance.

    The first call determines the default locale; later calls ignore the
    argument and return the existing instance.
    """
    global _singleton
    if _singleton is None:
        _singleton = Translator(default_locale or "en")
    return _singleton
