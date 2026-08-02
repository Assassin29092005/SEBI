"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    gemini_api_key: str = ""
    groq_api_key: str = ""
    llm_provider: str = "gemini"
    gemini_model: str = "gemini-2.0-flash"
    groq_model: str = "llama-3.3-70b-versatile"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    data_dir: Path = REPO_ROOT / "data"

    # Postgres connection string (SQLAlchemy async URL). Default matches
    # docker-compose.yml's postgres service exactly — `docker compose up -d`
    # plus `alembic upgrade head` is the entire local-dev setup, zero .env
    # edits required. Production sets DATABASE_URL in the environment.
    database_url: str = "postgresql+asyncpg://drhp:drhp_dev_password@localhost:5432/drhp_studio"

    # Auth: JWT bearer tokens + a persisted user store (see app.auth). Leaving
    # jwt_secret_key unset falls back to a per-process random key (app.auth.security)
    # — fine for local dev, but every restart invalidates existing sessions, so
    # production deployments must set JWT_SECRET_KEY in the environment.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480  # one working day

    # Self-registration is open for the "promoter" role (an SME signing up is
    # the primary path). Auditor/banker are intermediary roles with real
    # authority (certification, role-tagged uploads) — registering as either
    # requires the matching invite code. Blank code disables that role's
    # registration entirely rather than silently accepting any value.
    banker_invite_code: str = ""
    auditor_invite_code: str = ""

    # Security hardening: this app now handles real financial/legal documents
    # and runs auth over the network, so payload size is bounded at two
    # layers — see app.main's body-size middleware (checks Content-Length up
    # front, covers every JSON endpoint) and the upload endpoint's bounded
    # read loop (enforces the same cap even without/against a truthful
    # Content-Length header, e.g. chunked transfer-encoding).
    max_request_body_bytes: int = 20 * 1024 * 1024  # 20 MB — covers a scanned SME filing PDF

    # Encryption at rest (see app.crypto): archived original uploads are
    # written through it (facts/review/users now live in Postgres — see
    # app.db). Leaving
    # encryption_key unset falls back to a per-process random key, same
    # pattern as jwt_secret_key above — local dev/tests still round-trip
    # correctly with zero setup, but data written under an ephemeral key is
    # unreadable after any restart. Generate a real one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # Uploaded source documents (the original bytes a promoter/auditor/banker
    # submits) are archived here, encrypted, so a banker's due-diligence
    # review can go back to the original document a fact's snippet was pulled
    # from — not just the extracted text. See app.intake.vault.
    uploads_dir: Path = REPO_ROOT / "data" / "uploads"

    # Audit log: who accessed/changed what, and when — every request is
    # recorded (see app.audit + main.py's audit_log middleware), encrypted
    # at rest like everything else. GET /api/health is the only exclusion
    # (pure liveness-check noise, never security-relevant).
    audit_dir: Path = REPO_ROOT / "data" / "audit"

    # Litigation lookup (see app.intake.litigation): "indiankanoon" is the
    # one real API this connects to — api.indiankanoon.org, a real paid
    # service indexing published Supreme Court/High Court/tribunal
    # judgments (₹0.50/search page; new accounts get ₹500 free trial
    # credit at api.indiankanoon.org/signup/). It is NOT a live pending-case
    # docket — no free public API for that exists over Indian courts
    # (eCourts/NJDG has none); this connector can only surface litigation
    # that has already resulted in a published judgment. Blank provider
    # (default) or a missing token keeps the app on the offline mock — same
    # optional-real-integration-with-mandatory-fallback pattern as the LLM
    # provider (see app.llm.client).
    litigation_provider: str = ""
    indiankanoon_api_token: str = ""
    indiankanoon_max_results: int = 10

    # OCR for scanned/photographed documents (see app.intake.ocr): real SME
    # paperwork often has no machine-readable text layer at all. Requires a
    # real Tesseract OCR install on the machine running the backend — a
    # system binary, not a pip package, so it doesn't ship with this repo.
    # Missing/unreachable Tesseract means every OCR call raises
    # ``OcrUnavailable`` and extraction falls back to whatever native text
    # extraction produced (often nothing, for a genuinely scanned page) —
    # same optional-real-capability pattern as the LLM provider and the
    # litigation API. tesseract_cmd only needs setting when the binary isn't
    # on PATH (the common case on Windows: the installer doesn't add it).
    tesseract_cmd: str = ""
    tesseract_lang: str = "eng"

    # Error tracking (see app.observability): optional Sentry integration.
    # Blank DSN (default) means init_error_tracking() never calls
    # sentry_sdk.init() — zero behavior change, same optional-real-
    # integration pattern as everything else in this file. Get a free DSN at
    # sentry.io (or self-host) to start capturing unhandled exceptions and
    # logger.exception()/error() calls.
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_release: str = ""
    sentry_traces_sample_rate: float = 0.0

    # LLM cost/usage tracking (see app.llm_usage): every REAL (non-fallback)
    # call through app.llm.client.grounded_complete is recorded here —
    # encrypted at rest, same pattern as the audit log (app.audit).
    llm_usage_dir: Path = REPO_ROOT / "data" / "llm_usage"


settings = Settings()
