"""Shared dependency container passed to all handlers.

Keeping one object makes handler signatures tidy — every handler is a bound
method on a class that receives this ``BotDeps`` in its constructor.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.cache import RedisCache
from src.config import Settings
from src.databases import AbstractDatabase
from src.i18n import Translator


@dataclass(slots=True)
class BotDeps:
    settings: Settings
    db: AbstractDatabase
    cache: RedisCache
    translator: Translator
