"""FastAPI app for the Telegram webhook receiver.

Responsibilities — and only these:

1. Validate ``X-Telegram-Bot-Api-Secret-Token`` against the configured
   secret. Anything else is 401.
2. Deduplicate on ``update_id`` via the Phase-1 helper. Duplicates get
   200 without any publish (Telegram is told "thanks, already have it").
3. Publish the raw JSON body to RabbitMQ. Broker errors surface as 503
   so Telegram retries.
4. Expose ``/healthz`` (liveness — process is alive) and ``/readyz``
   (readiness — Redis + broker reachable).

Zero bot logic, zero DB, zero LLM. This service is meant to return 200
in tens of milliseconds so Telegram never times out.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from src.cache.idempotency import claim_update
from src.config import Settings
from src.tasks.metrics import (
    receiver_publish_duration_seconds,
    receiver_requests_total,
)

from .chat_id import extract_chat_id
from .publisher import PublisherError, UpdatePublisher

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings,
    redis: Redis,
    publisher: UpdatePublisher,
) -> FastAPI:
    """Wire a FastAPI app around the given dependencies.

    Dependencies are passed in (rather than instantiated here) so tests
    can swap in fakes without monkey-patching module globals.
    """
    app = FastAPI(
        title="tg-business-bot webhook receiver",
        # Telegram's body is small; no docs exposed in production.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post(settings.webhook_path)
    async def receive(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        # 1. Secret-token validation. An empty configured secret means
        #    "no check" — useful for local dev but unsafe in production;
        #    settings.py nudges operators to set one.
        expected = settings.webhook_secret_token
        if expected and x_telegram_bot_api_secret_token != expected:
            logger.warning("Rejected webhook with bad secret_token")
            raise HTTPException(status_code=401, detail="bad secret token")

        # 2. Parse body. Telegram always POSTs JSON.
        try:
            update: dict[str, Any] = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON") from None

        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise HTTPException(status_code=400, detail="missing update_id")

        # 3. Dedup. Reusing the Phase-1 helper guarantees the receiver
        #    and any legacy in-process path agree on what's a duplicate.
        first_time = await claim_update(
            redis, update_id, ttl_seconds=settings.dedup_ttl_seconds
        )
        if not first_time:
            logger.info("Duplicate update_id=%s dropped", update_id)
            receiver_requests_total.labels(outcome="dedup").inc()
            return Response(status_code=200)

        # 4. Publish. chat_id goes into the message headers so Phase 3
        #    can route on it without re-parsing the body.
        chat_id = extract_chat_id(update)
        try:
            with receiver_publish_duration_seconds.time():
                await publisher.publish(update, chat_id=chat_id)
        except PublisherError as exc:
            logger.error("Publish failed for update_id=%s: %s", update_id, exc)
            receiver_requests_total.labels(outcome="publish_error").inc()
            raise HTTPException(status_code=503, detail="broker unavailable") from exc

        receiver_requests_total.labels(outcome="published").inc()
        return Response(status_code=200)

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus scrape endpoint — exposes all in-process counters."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: process is up. Cheap, no dependencies checked."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        """Readiness: Redis + broker reachable.

        Kubernetes uses this to decide whether to send traffic. Keep the
        checks cheap — a single ``PING`` to Redis and the publisher's
        own connection flag.
        """
        try:
            await redis.ping()
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"redis unavailable: {exc}"
            ) from exc
        if not publisher.is_connected():
            raise HTTPException(status_code=503, detail="broker unavailable")
        return {"status": "ok"}

    return app


__all__ = ["create_app"]
