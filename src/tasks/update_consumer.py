"""Process-wide PTB ``Application`` held by the update-consumer worker.

Phase-3 topology: Celery workers consume one shard queue each and pass
raw update payloads to :func:`dispatch_update`, which feeds them into a
long-lived :class:`telegram.ext.Application` whose handlers are the same
ones that run in ``MODE=polling`` / ``MODE=webhook``.

Why a single long-lived app per worker process
----------------------------------------------
* Handler wiring is expensive (building filters, resolving command
  names, opening the Bot HTTP pool). Doing it per message would be
  wasteful and would hammer the Bot API with ``getMe`` calls.
* PTB's :meth:`Application.process_update` is the only supported way to
  dispatch an update through the registered handlers.
* One app per worker process (not per Celery task) keeps the event
  loop, DB session factory and Redis pool alive across invocations.

Per-chat ordering guarantee
---------------------------
Preserved by RabbitMQ — the consistent-hash exchange puts every update
from a given chat into the same shard queue, and each shard queue is
consumed by exactly one Celery worker with ``worker_concurrency=1``
and ``prefetch_multiplier=1``. This module assumes those constraints —
violating them silently reorders updates for a chat.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from telegram import Update

from src.cache import RedisCache, get_redis
from src.config import Settings, get_settings
from src.databases import create_database
from src.i18n import get_translator

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

# Module-level state: one per worker process. Celery forks after import
# so these singletons are initialised in ``worker_process_init``.
_app: "Application | None" = None
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _start_background_loop() -> asyncio.AbstractEventLoop:
    """Spawn a dedicated event loop on a daemon thread.

    Celery's worker runs sync Python by default; PTB's handlers are
    async. The cleanest bridge is a long-lived asyncio loop in a
    sidecar thread — we dispatch with ``run_coroutine_threadsafe`` from
    the Celery task and await the future synchronously.
    """
    loop = asyncio.new_event_loop()

    def _run() -> None:  # pragma: no cover — thread entry point
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_run, name="ptb-dispatch-loop", daemon=True)
    thread.start()

    global _loop_thread
    _loop_thread = thread
    return loop


async def _build_application(settings: Settings) -> "Application":
    """Create a PTB Application with all Phase-3 handlers wired up.

    Identical wiring to ``MODE=polling`` / ``MODE=webhook`` — the same
    :func:`src.bot.application.build_application` is reused so handler
    behaviour doesn't drift between dispatch paths. ``Application.initialize``
    is awaited here; ``start``/``stop`` are *not* — we don't need the
    updater, just the dispatcher.
    """
    # Import inside the function to avoid importing PTB at Celery boot
    # time for non-consumer workers (delivery, processing) that don't
    # need it.
    from src.bot.application import build_application
    from src.bot.deps import BotDeps

    translator = get_translator(settings.default_locale)

    db = create_database(settings)
    await db.connect()

    redis_client = get_redis(settings.redis_url)
    cache = RedisCache(redis_client, default_ttl=settings.redis_save_ttl)

    deps = BotDeps(settings=settings, db=db, cache=cache, translator=translator)
    application = build_application(deps)
    await application.initialize()
    logger.info("update-consumer PTB Application initialised")
    return application


def init_worker_process(settings: Settings | None = None) -> None:
    """Celery ``worker_process_init`` hook.

    Builds the PTB Application once per forked worker process. Must be
    called exactly once — subsequent calls are ignored to keep a single
    Bot HTTP pool.
    """
    global _app, _loop

    if _app is not None:
        logger.warning("init_worker_process called twice; ignoring")
        return

    settings = settings or get_settings()
    _loop = _start_background_loop()

    future = asyncio.run_coroutine_threadsafe(_build_application(settings), _loop)
    _app = future.result()


def shutdown_worker_process() -> None:
    """Celery ``worker_process_shutdown`` hook — tear down cleanly.

    Cleans the PTB Application (closes the Bot HTTP pool, DB pool, Redis
    pool), then stops the asyncio loop. Idempotent.
    """
    global _app, _loop, _loop_thread

    if _app is not None and _loop is not None:
        future = asyncio.run_coroutine_threadsafe(_app.shutdown(), _loop)
        try:
            future.result(timeout=10)
        except Exception as exc:  # pragma: no cover — best-effort shutdown
            logger.warning("PTB Application shutdown errored: %s", exc)

    if _loop is not None:
        _loop.call_soon_threadsafe(_loop.stop)
    if _loop_thread is not None:
        _loop_thread.join(timeout=5)

    _app = None
    _loop = None
    _loop_thread = None


def dispatch_update(raw_update: dict) -> None:
    """Hand a raw Telegram update to the PTB Application for dispatch.

    Blocks the calling (Celery) thread until ``process_update`` returns
    — that's what keeps per-chat ordering honest. Called from
    :func:`src.tasks.processing.handle_update`.
    """
    if _app is None or _loop is None:
        raise RuntimeError(
            "update-consumer not initialised; "
            "call init_worker_process() first (worker_process_init signal)"
        )

    update = Update.de_json(raw_update, _app.bot)
    if update is None:
        logger.warning(
            "Update.de_json returned None for update_id=%s",
            raw_update.get("update_id"),
        )
        return

    future = asyncio.run_coroutine_threadsafe(_app.process_update(update), _loop)
    future.result()  # propagate exceptions to Celery → triggers retries / DLQ


__all__ = [
    "dispatch_update",
    "init_worker_process",
    "shutdown_worker_process",
]
