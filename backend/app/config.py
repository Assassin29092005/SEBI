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

    persist_session: bool = True
    session_dir: Path = REPO_ROOT / "data" / "session"

    # Auth: JWT bearer tokens + a persisted user store (see app.auth). Leaving
    # jwt_secret_key unset falls back to a per-process random key (app.auth.security)
    # — fine for local dev, but every restart invalidates existing sessions, so
    # production deployments must set JWT_SECRET_KEY in the environment.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480  # one working day

    auth_dir: Path = REPO_ROOT / "data" / "auth"

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

    # Encryption at rest (see app.crypto): the session snapshot, the user
    # store, and archived original uploads are all written through it. Leaving
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


settings = Settings()
