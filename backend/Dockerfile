# Backend image: the FastAPI app served by uvicorn.
#
# Build from the REPO ROOT (not backend/), since it needs data/regulation and
# data/reference_drhps alongside backend/:
#   docker build -f backend/Dockerfile -t drhp-backend .
#
# Runtime configuration is entirely via environment variables — nothing
# secret is baked into this image. At minimum a real deployment sets
# DATABASE_URL, JWT_SECRET_KEY, and ENCRYPTION_KEY (see .env.example at the
# repo root for the full list; unset, the app falls back to dev-safe
# defaults that are NOT safe for real data — see each setting's comment in
# backend/app/config.py).
#
# DATA_DIR/UPLOADS_DIR/AUDIT_DIR are set explicitly below rather than left on
# their source-checkout-relative defaults — see the REPO_ROOT comment in
# backend/app/config.py for why a real (non-editable) install needs this.
# UPLOADS_DIR and AUDIT_DIR hold data that must outlive the container (the
# archived-upload vault and the audit log): mount /srv/var as a persistent
# volume in any real deployment, or that data vanishes on the next
# `docker run`.
#
# Migrations are NOT run automatically on container start — a real
# deployment runs them as an explicit, separate step, so a crash-looping
# container never re-runs migrations against a database mid-write. This same
# image can do it:
#   docker run --rm <env vars> drhp-backend \
#       python -m alembic -c backend/alembic.ini upgrade head

FROM python:3.12-slim

WORKDIR /srv

# Only what `pip install` and Alembic need — not the frontend, not tests,
# not the demo fixtures (data/demo_company is a dev/demo-only convenience,
# pushed through the real HTTP API by backend/scripts/seed_demo.py, never
# read directly by the running app).
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
COPY backend/migrations backend/migrations
COPY backend/alembic.ini backend/alembic.ini
COPY data/regulation data/regulation
COPY data/reference_drhps data/reference_drhps

RUN pip install --no-cache-dir ./backend

ENV DATA_DIR=/srv/data \
    UPLOADS_DIR=/srv/var/uploads \
    AUDIT_DIR=/srv/var/audit \
    API_HOST=0.0.0.0 \
    API_PORT=8000

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /srv/var/uploads /srv/var/audit \
    && chown -R appuser:appuser /srv/var
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
