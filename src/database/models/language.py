from sqlalchemy import Column, String

from sqlmodel import Field

from .base import Base


class Language(Base, table=True):
    """Available translation target languages.

    code    — ISO 639-1 code (e.g. "ru", "en", "de").
    name_key — i18n key used to look up the localised language name (e.g. "lang_ru").
    """

    __tablename__ = "languages"

    code: str = Field(sa_column=Column(String(10), primary_key=True))
    name_key: str = Field(sa_column=Column(String(64), nullable=False))

    def __repr__(self) -> str:
        return f"Language(code={self.code!r}, name_key={self.name_key!r})"
