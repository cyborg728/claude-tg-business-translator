"""Celery task queue (tasks_queue) and delivery queue (delivery_queue)."""

from .celery_app import celery_app

__all__ = ["celery_app"]
