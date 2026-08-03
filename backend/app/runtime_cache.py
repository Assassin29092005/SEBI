"""Process-local, non-durable runtime state: the generated-sections cache.

Not moved to Postgres: ``GET /api/sections`` has always returned "empty list
if never generated" with no special-cased restore path, and regeneration is
cheap/deterministic from confirmed facts (the actual source of truth, which
*is* durable). Known limitation: this cache is per-process, so it would not
be shared across multiple uvicorn workers/replicas — same limitation the old
in-memory ``AppState`` had.
"""

from __future__ import annotations

from app.generate.sections import GeneratedSection
from app.intake.litigation import FallbackLitigationConnector
from app.regulatory_watch import (
    RegulatoryWatchConnector,
    SebiIcdrWatchConnector,
    StalenessCheckResult,
)

_generated_sections: list[GeneratedSection] = []

# Real API (api.indiankanoon.org) when configured, offline mock
# otherwise/on failure — see app.intake.litigation for what this can and
# can't tell you (published judgments only, never a live docket). Stateless
# either way — recreated fresh only by reset_cache(), never by requests.
litigation_connector = FallbackLitigationConnector()

# Real scrape of SEBI's public ICDR-tagged postings — see
# app.regulatory_watch. A module-level instance (not reset by reset_cache(),
# same as litigation_connector above) so tests can monkeypatch it to a fake
# connector rather than hitting the live site on every test run.
regulatory_watch_connector: RegulatoryWatchConnector = SebiIcdrWatchConnector()

# Last regulatory-staleness check result (see app.regulatory_watch) — None
# until a banker explicitly triggers one via POST /api/regulatory-watch/check.
# Deliberately not auto-run on every request: it's a real external HTTP call
# against SEBI's public site, not something to fire on every page load.
_last_staleness_check: StalenessCheckResult | None = None


def get_generated_sections() -> list[GeneratedSection]:
    return _generated_sections


def set_generated_sections(sections: list[GeneratedSection]) -> None:
    global _generated_sections
    _generated_sections = sections


def get_last_staleness_check() -> StalenessCheckResult | None:
    return _last_staleness_check


def set_last_staleness_check(result: StalenessCheckResult) -> None:
    global _last_staleness_check
    _last_staleness_check = result


def reset_cache() -> None:
    """Test-only: clear the cache between cases."""
    global _generated_sections, _last_staleness_check
    _generated_sections = []
    _last_staleness_check = None
