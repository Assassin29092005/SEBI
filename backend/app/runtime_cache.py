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

_generated_sections: list[GeneratedSection] = []

# Real API (api.indiankanoon.org) when configured, offline mock
# otherwise/on failure — see app.intake.litigation for what this can and
# can't tell you (published judgments only, never a live docket). Stateless
# either way — recreated fresh only by reset_cache(), never by requests.
litigation_connector = FallbackLitigationConnector()


def get_generated_sections() -> list[GeneratedSection]:
    return _generated_sections


def set_generated_sections(sections: list[GeneratedSection]) -> None:
    global _generated_sections
    _generated_sections = sections


def reset_cache() -> None:
    """Test-only: clear the cache between cases."""
    global _generated_sections
    _generated_sections = []
