from __future__ import annotations

import logging
from typing import Any

from litestar import Litestar, Request, Response, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_401_UNAUTHORIZED,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis

from src.cache.idempotency import claim_update
from src.config import Settings
from src.metrics import receiver_publish_duration_seconds, receiver_requests_total

from .chat_id import extract_chat_id
from .publisher import PublisherError, UpdatePublisher

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings,
    redis: Redis,
    publisher: UpdatePublisher,
) -> Litestar:
    @post(settings.webhook_path)
    async def receive(request: Request) -> Response:
        expected = settings.webhook_secret_token
        actual = request.headers.get("x-telegram-bot-api-secret-token")
        if expected and actual != expected:
            logger.warning("Rejected webhook with bad secret_token")
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="bad secret token")

        try:
            update: dict[str, Any] = await request.json()
        except ValueError:
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="invalid JSON")

        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="missing update_id")

        first_time = await claim_update(
            redis, update_id, ttl_seconds=settings.dedup_ttl_seconds
        )
        if not first_time:
            logger.info("Duplicate update_id=%s dropped", update_id)
            receiver_requests_total.labels(outcome="dedup").inc()
            return Response(content=None, status_code=HTTP_200_OK)

        chat_id = extract_chat_id(update)
        try:
            with receiver_publish_duration_seconds.time():
                await publisher.publish(update, chat_id=chat_id)
        except PublisherError as exc:
            logger.error("Publish failed for update_id=%s: %s", update_id, exc)
            receiver_requests_total.labels(outcome="publish_error").inc()
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="broker unavailable"
            ) from exc

        receiver_requests_total.labels(outcome="published").inc()
        return Response(content=None, status_code=HTTP_200_OK)

    @get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    @get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @get("/readyz")
    async def readyz() -> dict[str, str]:
        try:
            await redis.ping()
        except Exception as exc:
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"redis unavailable: {exc}",
            ) from exc
        if not publisher.is_connected():
            raise HTTPException(
                status_code=HTTP_503_SERVICE_UNAVAILABLE, detail="broker unavailable"
            )
        return {"status": "ok"}

    return Litestar(
        route_handlers=[receive, metrics_endpoint, healthz, readyz],
        openapi_config=None,
    )


__all__ = ["create_app"]
