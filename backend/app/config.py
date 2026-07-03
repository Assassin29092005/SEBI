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
    gemini_model: str = "gemini-2.5-flash"  # 2.0-flash has zero free-tier quota
    groq_model: str = "llama-3.3-70b-versatile"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    data_dir: Path = REPO_ROOT / "data"


settings = Settings()
