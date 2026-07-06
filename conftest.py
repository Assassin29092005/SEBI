"""Make backend/app importable when running pytest from the repo root."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "backend"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_llm: opt in to real Gemini/Groq API calls (needs keys in .env); "
        "everything else runs the deterministic path",
    )


@pytest.fixture(autouse=True)
def _no_live_llm(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the deterministic path in every test by blanking the LLM API keys.

    Without this, a developer with keys in ``.env`` gets nondeterministic
    tests that silently make real network calls. ``get_provider`` raises
    ``LLMUnavailable`` when no key is set, which is exactly the offline
    fallback every LLM-dependent feature must have. Tests that intentionally
    exercise providers set fake keys themselves (test_llm_client.py) or patch
    ``grounded_complete``; tests that want the real network opt in with
    ``@pytest.mark.live_llm``.
    """
    if request.node.get_closest_marker("live_llm"):
        return
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")
