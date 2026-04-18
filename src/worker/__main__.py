from __future__ import annotations

import asyncio
import logging
import sys


_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=_LOG_FORMAT,
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    from .runner import run_worker

    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
