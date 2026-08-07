"""Make backend/app importable when running pytest from the repo root."""

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# A dedicated test database — never the dev DB from docker-compose.yml's
# ``drhp_studio`` — so the suite can never clobber a developer's local data.
# One-time setup: ``docker compose exec postgres createdb -U drhp drhp_studio_test``.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://drhp:drhp_dev_password@localhost:5433/drhp_studio_test",
)


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


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable rate limiting for the suite (see ``app.rate_limit``).

    The limiter's per-minute windows are wall-clock, but the suite fires
    hundreds of requests in seconds — every test after the first few would
    start collecting 429s purely because the tests are fast. ``test_rate_limit.py``
    re-enables it explicitly for the cases that actually exercise it.
    """
    from app.config import settings
    from app.rate_limit import reset_rate_limiter

    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    reset_rate_limiter()


# NullPool: pytest-asyncio gives each test function its own event loop by
# default, but asyncpg connections are bound to the loop they were opened
# on — a pooled connection reused across tests (i.e. across loops) raises
# "cannot perform operation: another operation is in progress". NullPool
# makes every checkout open a fresh physical connection on the loop that's
# actually running.
_test_engine: AsyncEngine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)


@pytest_asyncio.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    """One DB transaction per test, rolled back at the end — free isolation.

    Every commit a repo function issues (``app.facts_repo``, ``app.review.repo``,
    ``app.auth.store`` all call ``session.commit()`` internally) only releases a
    SAVEPOINT here, because the session is bound to a connection that already
    has an outer transaction open. The final ``conn.rollback()`` discards
    everything the test did, across as many HTTP requests as it issued.
    """
    async with _test_engine.connect() as conn:
        await conn.begin()
        maker = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with maker() as session:
            yield session
        await conn.rollback()
