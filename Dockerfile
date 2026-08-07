# Multi-stage build: Python backend + Node frontend → slim runtime
# Build frontend assets first, then copy into the backend image

# --------------------------------------------------------------------------
# Stage 1: Frontend build
# --------------------------------------------------------------------------
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --production=false
COPY frontend/ ./
RUN npm run build

# --------------------------------------------------------------------------
# Stage 2: Backend runtime
# --------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# System dependencies for Tesseract OCR (optional but recommended),
# PostgreSQL client (for pg_dump backups), and general build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    postgresql-client \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, in their own layer, so a source-only change doesn't
# re-resolve the whole dependency tree on every build.
COPY backend/pyproject.toml backend/
RUN pip install --no-cache-dir -e "backend/"

# Copy backend source
COPY backend/ backend/
COPY data/ data/

# Re-link the editable install now that backend/app actually exists. The
# install above ran against a directory containing only pyproject.toml, so
# setuptools' package discovery found nothing to map and `import app` would
# fail at runtime. --no-deps keeps this to a re-link, not a reinstall.
RUN pip install --no-cache-dir --no-deps -e "backend/"

# Built frontend — served by the API itself (see the StaticFiles mount at the
# bottom of app/main.py), so the image is one deployable unit.
COPY --from=frontend-build /app/frontend/dist frontend/dist

# Create data directories
RUN mkdir -p data/uploads data/audit out backups

# Environment defaults (override in production)
ENV API_HOST=0.0.0.0
ENV API_PORT=8000
ENV RATE_LIMIT_ENABLED=true

EXPOSE 8000

# Readiness, not just liveness: /api/health answers 200 with
# "status": "degraded" when the process is up but Postgres is unreachable,
# so the check asserts db_connected rather than merely that a response came
# back at all.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,urllib.request,sys; \
sys.exit(0 if json.load(urllib.request.urlopen('http://localhost:8000/api/health'))['db_connected'] else 1)"

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
