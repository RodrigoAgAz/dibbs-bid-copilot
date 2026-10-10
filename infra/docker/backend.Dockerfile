# Backend / worker / scheduler image (FastAPI + RQ + APScheduler + ML)
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend:/app/ml

# libgomp1: LightGBM runtime. curl: healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

# Chromium for the DIBBS consent-banner session (live mode only). Set to 0 to skip (~400 MB).
ARG INSTALL_PLAYWRIGHT=1
RUN if [ "$INSTALL_PLAYWRIGHT" = "1" ]; then python -m playwright install --with-deps chromium; fi

COPY backend /app/backend
COPY ml /app/ml

EXPOSE 8000
# Binds "::" (dual-stack) so platform private networks over IPv6 work (e.g. Railway); PORT is set by the host.
CMD ["sh", "-c", "python -m app.cli bootstrap && uvicorn app.main:app --host :: --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
