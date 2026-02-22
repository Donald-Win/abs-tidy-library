# ──────────────────────────────────────────────────────────────────────────────
# ABS Tidy Library – Dockerfile
# Builds a lightweight image running the Flask web UI via Gunicorn.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Metadata
LABEL org.opencontainers.image.title="ABS Tidy Library"
LABEL org.opencontainers.image.description="Audiobookshelf Library Organiser — Web UI"
LABEL org.opencontainers.image.url="https://github.com/Donald-Win/abs-tidy-library"
LABEL org.opencontainers.image.source="https://github.com/Donald-Win/abs-tidy-library"
LABEL maintainer="Donald-Win"

# ── System deps ───────────────────────────────────────────────────────────────
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl \
 && rm -rf /var/lib/apt/lists/*

# ── Python deps ───────────────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY app/ .

# ── Runtime config ────────────────────────────────────────────────────────────
# Default library path (override with -e LIBRARY_PATH=/your/path)
ENV LIBRARY_PATH=/library \
    PORT=8080 \
    PYTHONUNBUFFERED=1

# Expose web port
EXPOSE 8080

# Library volume mount point
VOLUME ["/library"]

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/ || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Gunicorn with threading worker to support SSE streaming
CMD gunicorn \
      --bind "0.0.0.0:${PORT}" \
      --worker-class gthread \
      --workers 1 \
      --threads 8 \
      --timeout 300 \
      --access-logfile - \
      --error-logfile - \
      web:app
