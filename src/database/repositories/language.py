from abc import abstractmethod

from sqlalchemy import select

from ..models import Language
from .base import BaseRepository

# Default languages seeded on first startup.
_DEFAULT_LANGUAGES: list[tuple[str, str]] = [
    ("ru", "lang_ru"),
    ("en", "lang_en"),
    ("de", "lang_de"),
    ("fr", "lang_fr"),
    ("es", "lang_es"),
    ("zh", "lang_zh"),
    ("ja", "lang_ja"),
    ("ar", "lang_ar"),
    ("tr", "lang_tr"),
    ("it", "lang_it"),
    ("pt", "lang_pt"),
    ("ko", "lang_ko"),
]


class ILanguageRepository(BaseRepository[Language]):
    """Interface for the languages repository."""

    @abstractmethod
    async def list_all(self) -> list[Language]:
        """Return all available languages."""
        ...

    @abstractmethod
    async def seed_if_empty(self) -> None:
        """Populate the table with default languages if it is empty."""
        ...


class LanguageRepository(ILanguageRepository):
    """SQLite implementation of ILanguageRepository."""

    async def list_all(self) -> list[Language]:
        async with self._session() as sess:
            result = await sess.execute(select(Language).order_by(Language.code))
            return list(result.scalars().all())

    async def seed_if_empty(self) -> None:
        async with self._session() as sess:
            existing = await sess.execute(select(Language).limit(1))
            if existing.scalars().first() is not None:
                return
            for code, name_key in _DEFAULT_LANGUAGES:
                sess.add(Language(code=code, name_key=name_key))
