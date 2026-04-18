from __future__ import annotations

import asyncio
import logging
import sys


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def main() -> None:
    _setup_logging()
    from src.receiver import run_receiver

    try:
        asyncio.run(run_receiver())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
