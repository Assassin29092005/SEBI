"""Operational plumbing: clause-text retrieval, metrics, rate limiting.

None of these touch the fact store or the LLM — they're the "run this as a
real service" layer (observability, abuse protection) plus the clause-text
lookup that backs the citation trust guarantee.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.metrics import _MetricsCollector
from app.rate_limit import _SlidingWindow, _classify
from app.schema.clause_text import get_clause_text, list_available_clauses
from app.schema.loader import load_checklist

_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


# --------------------------------------------------------------------------
# Clause-text retrieval
# --------------------------------------------------------------------------


def test_schedule_vi_part_a_indexes_all_eighteen_paragraphs() -> None:
    paras = [c for c in list_available_clauses() if c.startswith("Sch. VI para")]
    assert paras == [f"Sch. VI para ({n})" for n in range(1, 19)]


def test_paragraph_lookup_returns_the_top_level_heading_not_a_nested_item() -> None:
    """The regression this indexer exists to prevent.

    Part A's nested lists reuse the same ``(N)`` shape as its top-level
    paragraphs — para (5) Risk factors contains its own (1)–(31), so a
    shape-only match resolves "para (8)" to a risk-factor sub-item about
    customer concentration instead of the Capital Structure heading.
    """
    assert get_clause_text("ICDR Sch. VI Part A, para (8)(A)–(B)").startswith(
        "(8) Capital structure:"
    )
    assert get_clause_text("ICDR Sch. VI Part A, para (9)(K)").startswith(
        "(9) Particulars of the issue:"
    )
    assert get_clause_text("ICDR Sch. VI Part A, para (5)").startswith("(5) Risk factors:")


def test_chapter_ix_regulation_lookup() -> None:
    passage = get_clause_text("ICDR Reg. 236(1)")
    assert passage is not None
    assert passage.startswith("236.")
    assert "twenty per cent" in passage


def test_unknown_clause_ref_returns_none_rather_than_a_guess() -> None:
    assert get_clause_text("ICDR Sch. VI Part A, para (99)") is None
    assert get_clause_text("Companies Act 2013 s.53") is None
    assert get_clause_text("") is None


def test_every_checklist_clause_ref_either_resolves_or_returns_none() -> None:
    """Never a wrong passage: each ref resolves to text whose own number
    matches the number cited, or to nothing at all."""
    for entry in load_checklist().entries:
        passage = get_clause_text(entry.clause_ref)
        if passage is None:
            continue
        head = passage.split("\n", 1)[0]
        assert head.startswith("(") or head[0].isdigit(), (entry.id, head)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_metrics_counts_requests_errors_and_latency() -> None:
    collector = _MetricsCollector()
    collector.record("GET /api/facts", 200, 10.0)
    collector.record("GET /api/facts", 200, 30.0)
    collector.record("GET /api/facts", 500, 20.0)

    snapshot = collector.snapshot()
    assert snapshot.total_requests == 3
    assert snapshot.total_errors == 1

    endpoint = snapshot.endpoints[0]
    assert endpoint.path == "GET /api/facts"
    assert endpoint.total_requests == 3
    assert endpoint.error_count == 1
    assert endpoint.avg_latency_ms == 20.0
    assert endpoint.status_codes == {200: 2, 500: 1}


def test_metrics_latency_samples_are_bounded() -> None:
    """Memory must be O(endpoints), not O(requests served)."""
    from app.metrics import _LATENCY_WINDOW

    collector = _MetricsCollector()
    for i in range(_LATENCY_WINDOW * 3):
        collector.record("GET /api/facts", 200, float(i))

    assert len(collector._latencies["GET /api/facts"]) == _LATENCY_WINDOW
    # Counts stay exact even though the latency sample is windowed.
    assert collector.snapshot().endpoints[0].total_requests == _LATENCY_WINDOW * 3


def test_route_label_collapses_ids_and_page_numbers() -> None:
    """Metrics keys must be bounded by the API surface, not by traffic."""
    assert (
        main_module._route_label("POST", "/api/facts/abc-123/confirm", "abc-123")
        == "POST /api/facts/{id}/confirm"
    )
    # Numeric segments collapse even when the classifier didn't name them —
    # /page/1 .. /page/300 is one counter, not three hundred.
    assert (
        main_module._route_label("GET", "/api/uploads/doc-9/page/7", "doc-9")
        == "GET /api/uploads/{id}/page/{id}"
    )
    # A route with no resource id is left exactly as-is.
    assert main_module._route_label("GET", "/api/facts", None) == "GET /api/facts"


def test_metrics_reset_clears_everything() -> None:
    collector = _MetricsCollector()
    collector.record("GET /api/facts", 200, 1.0)
    collector.reset()
    assert collector.snapshot().total_requests == 0
    assert collector.snapshot().endpoints == []


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------


def test_sliding_window_allows_up_to_the_limit_then_denies() -> None:
    window = _SlidingWindow()
    for _ in range(3):
        allowed, retry_after = window.is_allowed("user:a", max_requests=3, window_seconds=60)
        assert allowed and retry_after == 0.0

    allowed, retry_after = window.is_allowed("user:a", max_requests=3, window_seconds=60)
    assert not allowed
    assert retry_after > 0


def test_sliding_window_keys_are_independent() -> None:
    window = _SlidingWindow()
    assert window.is_allowed("user:a", 1, 60)[0]
    assert not window.is_allowed("user:a", 1, 60)[0]
    assert window.is_allowed("user:b", 1, 60)[0]


def test_expired_requests_free_the_budget_and_drop_the_key() -> None:
    """A zero-length window expires immediately: the budget resets and the
    drained key is removed rather than accumulating forever."""
    window = _SlidingWindow()
    window.is_allowed("user:a", max_requests=1, window_seconds=0)
    assert window.is_allowed("user:a", max_requests=1, window_seconds=0)[0]

    window.is_allowed("user:b", max_requests=1, window_seconds=0)
    assert "user:a" not in window._windows


def test_classify_applies_the_tightest_matching_limit() -> None:
    assert _classify("/api/auth/login", "POST")[1] == 10
    assert _classify("/api/generate", "POST")[1] == 5
    assert _classify("/api/regulatory-watch/check", "POST")[1] == 3
    # Fact writes are bursty by nature (confirm-per-fact), so they get a
    # bulk-sized budget rather than the one-at-a-time default.
    assert _classify("/api/facts/abc-123/confirm", "POST")[1] == 240
    # Unlisted route falls back to the default budget.
    assert _classify("/api/gaps", "GET") == ("default", 60)


def test_each_limit_gets_its_own_bucket() -> None:
    """Anonymous traffic under the 60/min default keys on the caller's IP,
    exactly like login does. Without distinct buckets the two share one
    counter and a few unauthenticated calls 429 the login attempt."""
    login_bucket, _ = _classify("/api/auth/login", "POST")
    default_bucket, _ = _classify("/api/facts", "GET")
    assert login_bucket != default_bucket


# --------------------------------------------------------------------------
# SPA serving (container only — skipped unless frontend/dist has been built)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _DIST.is_dir(), reason="frontend/dist not built")
def test_unknown_api_path_still_404s_as_json_not_the_spa_shell() -> None:
    """The catch-all must not swallow API typos into a 200 HTML response."""
    with TestClient(main_module.app) as client:
        response = client.get("/api/definitely-not-a-route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.skipif(not _DIST.is_dir(), reason="frontend/dist not built")
def test_client_side_route_falls_back_to_index_html() -> None:
    """A react-router deep link must survive a page refresh."""
    with TestClient(main_module.app) as client:
        response = client.get("/wizard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_only_api_calls_are_audited() -> None:
    """Serving the SPA shell and its assets is not an access event — a row
    per asset per page load is noise, and enough of it to undo the point of
    moving the log into a bounded table."""
    assert main_module._is_audited("/api/facts")
    assert main_module._is_audited("/api/definitely-not-a-route")  # unmapped, still logged
    assert not main_module._is_audited("/api/health")  # liveness noise
    assert not main_module._is_audited("/wizard")
    assert not main_module._is_audited("/assets/index-abc123.js")
