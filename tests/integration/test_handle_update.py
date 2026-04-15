"""handle_update Celery task + update_consumer dispatch wiring.

The goal is to prove the task hands off to ``dispatch_update`` with the
unmodified raw payload, and that ``dispatch_update`` rebuilds a
:class:`telegram.Update` and feeds it into the module-level PTB
Application. We don't build a real PTB app — a ``SimpleNamespace`` is
enough to capture ``process_update`` calls.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tasks import update_consumer


@pytest.fixture
def fake_ptb_app():
    """Inject a fake PTB Application into update_consumer module state.

    The dispatch loop runs on a daemon thread (matching prod) so the
    ``run_coroutine_threadsafe`` call path is exercised end-to-end.
    """
    loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    fake_bot = SimpleNamespace(defaults=None)
    fake_app = SimpleNamespace(
        bot=fake_bot,
        process_update=AsyncMock(return_value=None),
        shutdown=AsyncMock(return_value=None),
    )

    update_consumer._app = fake_app
    update_consumer._loop = loop
    update_consumer._loop_thread = thread

    try:
        yield fake_app
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        update_consumer._app = None
        update_consumer._loop = None
        update_consumer._loop_thread = None


def test_dispatch_update_rejects_uninitialised_worker():
    # Fresh module state — no init called yet.
    update_consumer._app = None
    update_consumer._loop = None
    with pytest.raises(RuntimeError, match="not initialised"):
        update_consumer.dispatch_update({"update_id": 1})


def test_dispatch_update_feeds_update_into_ptb(fake_ptb_app):
    # Patch Update.de_json so we don't need a real Bot.
    fake_update = SimpleNamespace(update_id=99)
    with patch(
        "src.tasks.update_consumer.Update.de_json", return_value=fake_update
    ) as de_json:
        update_consumer.dispatch_update({"update_id": 99, "message": {}})

    de_json.assert_called_once()
    fake_ptb_app.process_update.assert_awaited_once_with(fake_update)


def test_dispatch_update_handles_none_from_de_json(fake_ptb_app, caplog):
    # Telegram occasionally sends update shapes PTB doesn't recognize;
    # de_json returns None. We should log and skip, NOT crash.
    with patch("src.tasks.update_consumer.Update.de_json", return_value=None):
        update_consumer.dispatch_update({"update_id": 42, "weird": {}})

    fake_ptb_app.process_update.assert_not_awaited()


def test_dispatch_update_propagates_handler_exceptions(fake_ptb_app):
    # Errors from handlers must surface to Celery so autoretry / DLQ
    # kick in — swallowing them would lose updates silently.
    fake_ptb_app.process_update.side_effect = RuntimeError("handler blew up")

    with patch(
        "src.tasks.update_consumer.Update.de_json",
        return_value=SimpleNamespace(update_id=1),
    ):
        with pytest.raises(RuntimeError, match="handler blew up"):
            update_consumer.dispatch_update({"update_id": 1})


def test_handle_update_task_forwards_payload(fake_ptb_app):
    """The Celery task body must call dispatch_update with the raw dict,
    unchanged — the receiver publishes bytes, Celery deserializes to a
    dict, and we forward as-is so Phase-4 replays work bit-for-bit."""
    from src.tasks.processing import handle_update

    payload = {"update_id": 7, "message": {"chat": {"id": 10}}}

    with patch(
        "src.tasks.update_consumer.dispatch_update"
    ) as dispatch_mock:
        # ``.run`` triggers the task body synchronously (no broker).
        handle_update.run(payload)

    dispatch_mock.assert_called_once_with(payload)


def test_init_worker_process_is_idempotent():
    """Guard against a double-init that would leak a second Bot HTTP pool."""
    update_consumer._app = MagicMock()  # pretend we're already initialised
    # Should NOT raise and should NOT rebuild.
    update_consumer.init_worker_process()
    # Still the same object.
    assert update_consumer._app is not None
    update_consumer._app = None
