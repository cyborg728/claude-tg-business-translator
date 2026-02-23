import asyncio
import logging

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

# Maximum attempts for a single Gemini call.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


class TranslationService:
    """Wraps Google Gemini to provide async translation and language detection."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    # ── Public API ────────────────────────────────────────────────────────────

    async def translate(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
    ) -> str:
        """Translate *text* into *target_language*.

        Parameters
        ----------
        text:
            The text to translate.
        target_language:
            ISO 639-1 code or full language name (e.g. ``"ru"`` or ``"Russian"``).
        source_language:
            Optional hint for the source language. When omitted Gemini
            auto-detects it.

        Returns
        -------
        str
            The translated text only, with no extra commentary.
        """
        if source_language:
            prompt = (
                f"Translate the following text from {source_language} to {target_language}. "
                "Return ONLY the translated text — no explanations, no quotes, no prefixes:\n\n"
                f"{text}"
            )
        else:
            prompt = (
                f"Translate the following text to {target_language}. "
                "Return ONLY the translated text — no explanations, no quotes, no prefixes:\n\n"
                f"{text}"
            )

        response = await self._call_gemini(prompt)
        return response.strip()

    async def detect_language(self, text: str) -> str:
        """Return the ISO 639-1 code of *text*'s language.

        Falls back to ``"en"`` on failure.
        """
        prompt = (
            "Detect the language of the following text. "
            "Reply with ONLY the ISO 639-1 two-letter language code (e.g. 'en', 'ru', 'de'). "
            "Nothing else:\n\n"
            f"{text}"
        )
        try:
            result = await self._call_gemini(prompt)
            code = result.strip().lower()[:5]  # safety truncation
            return code if code else "en"
        except Exception as exc:
            logger.warning("Language detection failed: %s — defaulting to 'en'", exc)
            return "en"

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _call_gemini(self, prompt: str) -> str:
        """Call the Gemini API with simple exponential-backoff retry logic."""
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        temperature=0.1,  # low temp for deterministic translation
                    ),
                )
                return response.text or ""
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "Gemini call failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt,
                        _MAX_RETRIES,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(f"Gemini API unavailable after {_MAX_RETRIES} attempts") from last_exc
