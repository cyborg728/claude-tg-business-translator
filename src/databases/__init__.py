"""Database layer.

Split in two packages:
  * :mod:`src.databases.interfaces` — backend-agnostic abstract base classes
    (``AbstractDatabase`` + one ``I<Entity>Repository`` per entity).
  * :mod:`src.databases.sqlite`     — concrete SQLAlchemy + SQLite
    implementation (models, repositories, database class).

Adding a new backend (Postgres, MySQL, …) means creating a sibling package
(e.g. ``src.databases.postgres``) implementing the same interfaces and wiring
it up in :func:`create_database`.
"""

from .factory import create_database
from .interfaces import (
    AbstractDatabase,
    IBusinessConnectionRepository,
    IKvStoreRepository,
    IMessageMappingRepository,
    IUserRepository,
)

__all__ = [
    "AbstractDatabase",
    "IBusinessConnectionRepository",
    "IKvStoreRepository",
    "IMessageMappingRepository",
    "IUserRepository",
    "create_database",
]
