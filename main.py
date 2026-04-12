"""Entry point for the Telegram Business Bot.

Usage
-----
Polling (default / development)::

    python main.py

Webhook (production)::

    MODE=webhook WEBHOOK_BASE_URL=https://example.f8f.dev python main.py

All configuration is read from environment variables or a ``.env`` file.
See ``.env.example`` for the full list.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from src.bot import run_bot

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "telegram.vendor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    _setup_logging()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
