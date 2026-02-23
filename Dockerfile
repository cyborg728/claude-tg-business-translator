FROM python:3.12-slim

# Non-root user for security.
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/sh --create-home appuser

WORKDIR /app

# Install dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source.
COPY . .

# The SQLite database lives in this directory; mount a PersistentVolume here.
RUN mkdir -p /app/data && chown -R appuser:appgroup /app/data

USER appuser

# Expose the webhook port (only used when MODE=webhook).
EXPOSE 8080

ENTRYPOINT ["python", "main.py"]
