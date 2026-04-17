"""Entry point for the Telegram Business Bot.

Usage
-----
Polling (default / local development)::

    python main.py

Receiver (production — stateless FastAPI webhook handler)::

    MODE=receiver WEBHOOK_BASE_URL=https://example.f8f.dev python main.py

All configuration is read from environment variables or a ``.env`` file.
See ``.env.example`` for the full list.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from src.config import get_settings

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
    settings = get_settings()
    try:
        if settings.mode == "receiver":
            # Receiver runs a FastAPI app — no PTB, no DB. Lazy-imported
            # to keep bot-mode startup quick and surface-area minimal.
            from src.receiver import run_receiver

            asyncio.run(run_receiver(settings))
        else:
            from src.bot import run_bot

            asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
