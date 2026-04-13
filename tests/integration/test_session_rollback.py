"""Session helper rolls back on exception, commits otherwise."""

from __future__ import annotations

import pytest

from src.databases.sqlite.models import UserModel


async def test_exception_inside_session_rolls_back(sqlite_db):
    repo = sqlite_db.users

    with pytest.raises(RuntimeError):
        async with repo._session() as sess:
            sess.add(
                UserModel(
                    telegram_user_id=777,
                    username="will-be-rolled-back",
                    first_name="Nope",
                    last_name=None,
                    language_code="en",
                )
            )
            await sess.flush()
            raise RuntimeError("boom")

    # Row must NOT have been persisted.
    assert await repo.get_by_telegram_id(777) is None
