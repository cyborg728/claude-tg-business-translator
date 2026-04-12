from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Language(Base):
    """Available translation target languages.

    code     — ISO 639-1 code (e.g. "ru", "en", "de").
    name_key — i18n key used to look up the localised language name (e.g. "lang_ru").
    """

    __tablename__ = "languages"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name_key: Mapped[str] = mapped_column(String(64))

    def __repr__(self) -> str:
        return f"Language(code={self.code!r}, name_key={self.name_key!r})"
