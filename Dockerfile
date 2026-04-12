# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps (kept minimal — SQLite ships with glibc)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (for layer caching)
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# App source
COPY . .

# Create runtime data dir
RUN mkdir -p /app/data

# Non-root user
RUN useradd --create-home --uid 1000 botuser \
    && chown -R botuser:botuser /app
USER botuser

# Default: run the bot. Override with:
#   celery -A src.tasks.celery_app worker -Q tasks_queue     (processing worker)
#   celery -A src.tasks.celery_app worker -Q delivery_queue  (delivery worker)
CMD ["python", "main.py"]
