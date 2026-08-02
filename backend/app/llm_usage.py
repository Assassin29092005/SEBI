"""LLM cost & usage tracking: every REAL (non-fallback) LLM call, recorded.

Wired into ``app.llm.client.grounded_complete`` — the single choke point
every LLM-dependent feature already goes through (``app.generate.sections``,
``app.intake.uploads``, ``app.validate.contradictions``,
``app.validate.examiner``). Calls that never reach a real provider
(``LLMUnavailable``, no key configured, the deterministic fallback path) are
never recorded — there is nothing to bill for a call that didn't happen.

Storage: same flat-encrypted-file, atomic-write, banker-only-review pattern
as ``app.audit`` (see its module docstring for the O(n)-per-write caveat
this inherits too — fine for one issuer's LLM call volume over a drafting
cycle). ``GET /api/llm-usage`` (banker-only) is the review surface.

Pricing (``_PRICING_PER_MILLION_TOKENS`` below) is a hardcoded, dated
snapshot — LLM provider pricing changes without notice and this app has no
live pricing API to query. A model not in the table gets ``cost_usd=None``
rather than a silently wrong ``$0`` — an unpriced call should look unpriced,
not free. Treat every reported total as directional, not a bill.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import settings
from app.crypto import DecryptionError, decrypt_bytes, encrypt_bytes

# ``.enc`` (not ``.json``): the file is ciphertext, not readable JSON.
LLM_USAGE_FILENAME = "llm_usage.enc"

# USD per 1,000,000 tokens, (input, output). Sourced from each provider's
# public pricing page, pinned as of this code's authorship — provider
# pricing changes without notice; verify before relying on this for real
# budgeting, and add a new entry here whenever Settings.gemini_model /
# Settings.groq_model changes to a model not already listed.
_PRICING_PER_MILLION_TOKENS: dict[tuple[str, str], tuple[float, float]] = {
    ("gemini", "gemini-2.0-flash"): (0.10, 0.40),  # ai.google.dev/pricing
    ("groq", "llama-3.3-70b-versatile"): (0.59, 0.79),  # groq.com/pricing
}


def estimate_cost_usd(
    provider: str, model: str, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    """``None`` when either token count or the (provider, model) pricing is unknown."""
    if input_tokens is None or output_tokens is None:
        return None
    pricing = _PRICING_PER_MILLION_TOKENS.get((provider, model))
    if pricing is None:
        return None
    input_price, output_price = pricing
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


class LlmUsageEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Which caller made this call, e.g. "extract_facts", "generate_section",
    # "contradiction_refine", "semantic_check", "examiner" — lets cost be
    # broken down by feature, not just by provider/model.
    feature: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class LlmUsageByFeature(BaseModel):
    feature: str
    calls: int
    input_tokens: int
    output_tokens: int
    # None only when every call in this feature has unknown pricing —
    # distinguished from a genuine $0 by LlmUsageSummary.calls_with_unknown_pricing.
    cost_usd: float | None


class LlmUsageSummary(BaseModel):
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float | None  # None only when total_calls == 0
    calls_with_unknown_pricing: int
    by_feature: list[LlmUsageByFeature]


class _UsageFile(BaseModel):
    events: list[LlmUsageEvent] = []


class LlmUsageLog:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._events: list[LlmUsageEvent] = []
        self._load()

    def _path(self) -> Path:
        base = self._directory if self._directory is not None else settings.llm_usage_dir
        return base / LLM_USAGE_FILENAME

    def _load(self) -> None:
        path = self._path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            plaintext = decrypt_bytes(raw)
        except DecryptionError:
            return
        try:
            data = _UsageFile.model_validate_json(plaintext)
        except ValueError:
            return
        self._events = list(data.events)

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        plaintext = _UsageFile(events=self._events).model_dump_json().encode("utf-8")
        tmp.write_bytes(encrypt_bytes(plaintext))
        os.replace(tmp, path)

    def record(self, event: LlmUsageEvent) -> LlmUsageEvent:
        self._events.append(event)
        self._save()
        return event

    def list_events(
        self, *, feature: str | None = None, provider: str | None = None, limit: int = 500
    ) -> list[LlmUsageEvent]:
        """Most recent first, optionally filtered. ``limit`` caps the response size."""
        results = self._events
        if feature:
            results = [e for e in results if e.feature == feature]
        if provider:
            results = [e for e in results if e.provider == provider]
        return sorted(results, key=lambda e: e.at, reverse=True)[:limit]

    def summary(self) -> LlmUsageSummary:
        events = self._events
        by_feature: dict[str, list[LlmUsageEvent]] = {}
        for event in events:
            by_feature.setdefault(event.feature, []).append(event)

        feature_rows = [
            LlmUsageByFeature(
                feature=feature,
                calls=len(evs),
                input_tokens=sum(e.input_tokens or 0 for e in evs),
                output_tokens=sum(e.output_tokens or 0 for e in evs),
                cost_usd=(
                    sum(e.cost_usd for e in evs if e.cost_usd is not None)
                    if any(e.cost_usd is not None for e in evs)
                    else None
                ),
            )
            for feature, evs in sorted(by_feature.items())
        ]
        return LlmUsageSummary(
            total_calls=len(events),
            total_input_tokens=sum(e.input_tokens or 0 for e in events),
            total_output_tokens=sum(e.output_tokens or 0 for e in events),
            total_cost_usd=(
                sum(e.cost_usd for e in events if e.cost_usd is not None)
                if any(e.cost_usd is not None for e in events)
                else None
            ),
            calls_with_unknown_pricing=sum(1 for e in events if e.cost_usd is None),
            by_feature=feature_rows,
        )


_log: LlmUsageLog | None = None


def get_llm_usage_log() -> LlmUsageLog:
    """Process-wide singleton, lazily created (and lazily loaded from disk)."""
    global _log
    if _log is None:
        _log = LlmUsageLog()
    return _log


def reset_llm_usage_log() -> LlmUsageLog:
    """Swap in a fresh log — used by tests after monkeypatching ``settings.llm_usage_dir``."""
    global _log
    _log = LlmUsageLog()
    return _log
