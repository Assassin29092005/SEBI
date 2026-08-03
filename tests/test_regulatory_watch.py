"""Regulatory-staleness watcher (app.regulatory_watch).

The HTML fixture below mirrors SEBI's real results table structure exactly
(verified live against sebi.gov.in/sebiweb/home/HomeAction.do?doListingAll=
yes&search=ICDR during development — same table id, same nested "click here
to comment" anchor embedded as literal text inside the title attribute,
same "MMM DD, YYYY" date format) — a minimal, hand-built sample rather than
the full ~55KB captured page, kept in the test file for speed and
readability, the same way test_ocr.py/test_uploads.py build minimal
synthetic PDFs rather than storing large downloaded fixtures.

``test_live_sebi_site_is_genuinely_reachable_and_parseable`` is the one
real, network-hitting test — it self-skips (never fails the suite) if
sebi.gov.in is unreachable or has changed shape, the same resilience
philosophy ``test_ocr.py`` uses for a missing Tesseract install.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx
import pytest
from app.regulatory_watch import (
    RegulatoryUpdate,
    RegulatoryWatchUnavailable,
    SebiIcdrWatchConnector,
    StalenessCheckResult,
    _clean_title,
    _ResultsTableParser,
    check_for_staleness,
)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _sample_html(rows: str) -> str:
    return f"""
    <table class='table' id='sample_1'>
      <thead><tr><th>Date</th><th>Category</th><th>Title</th></tr></thead>
      <tbody>
      {rows}
      </tbody>
    </table>
    """


def _row(date_text: str, title: str, href: str, category: str = "Reports") -> str:
    comment_link = (
        "<a href='https://www.sebi.gov.in/sebiweb/publiccommentv2/"
        "PublicCommentAction.do?doPublicComments=yes' target='_blank' "
        "style='color:#007ffc'> Click here to provide your comments </a>"
    )
    full_title_attr = f"{title} {comment_link}"
    return (
        f"<tr role='row' class='odd'>"
        f"<td>{date_text}</td><td>{category}</td>"
        f'<td><a href="{href}" target="_blank" title="{full_title_attr}" '
        f'class="points"> {title} {comment_link}</a></td>'
        f"</tr>"
    )


SAMPLE_HTML = _sample_html(
    _row(
        "Feb 09, 2026",
        "Review of minimum value of investment under SEBI ICDR Regulations, 2018",
        "https://www.sebi.gov.in/reports/feb-2026/review-of-minimum-value_1.html",
    )
    + _row(
        "Mar 20, 2025",
        "Consultation Paper on certain Amendments to SEBI (ICDR) Regulations, 2018",
        "https://www.sebi.gov.in/reports/mar-2025/consultation-paper-icdr_2.html",
    )
    + _row(
        "Nov 19, 2024",
        "Consultation paper on Review of SME segment framework under SEBI (ICDR) Regulations, 2018",
        "https://www.sebi.gov.in/reports/nov-2024/sme-segment-framework_3.html",
    )
)


class _FakeAsyncClient:
    """Duck-typed stand-in for httpx.AsyncClient — only .get() is used."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self._text = text
        self._status_code = status_code

    async def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(self._status_code, text=self._text, request=httpx.Request("GET", url))


# --------------------------------------------------------------------------
# _ResultsTableParser — real HTML parsing, verified structure
# --------------------------------------------------------------------------


def test_parser_extracts_every_row_in_order() -> None:
    parser = _ResultsTableParser()
    parser.feed(SAMPLE_HTML)
    assert len(parser.rows) == 3
    dates = [r[0] for r in parser.rows]
    assert dates == ["Feb 09, 2026", "Mar 20, 2025", "Nov 19, 2024"]


def test_parser_captures_href_and_raw_title_with_boilerplate_still_attached() -> None:
    parser = _ResultsTableParser()
    parser.feed(SAMPLE_HTML)
    _date_text, title, href = parser.rows[0]
    assert href == "https://www.sebi.gov.in/reports/feb-2026/review-of-minimum-value_1.html"
    assert "Click here to provide your comments" in title  # not yet cleaned


def test_clean_title_strips_the_trailing_comment_boilerplate() -> None:
    parser = _ResultsTableParser()
    parser.feed(SAMPLE_HTML)
    _, raw_title, _ = parser.rows[0]
    cleaned = _clean_title(raw_title)
    assert "Click here to provide your comments" not in cleaned
    assert "Review of minimum value of investment" in cleaned


def test_parser_ignores_rows_outside_tbody() -> None:
    html = "<table id='sample_1'><thead><tr role='row'><td>ignored</td></tr></thead></table>"
    parser = _ResultsTableParser()
    parser.feed(html)
    assert parser.rows == []


# --------------------------------------------------------------------------
# SebiIcdrWatchConnector.check_for_updates
# --------------------------------------------------------------------------


def test_check_for_updates_filters_strictly_newer_than_since() -> None:
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(SAMPLE_HTML))
    updates = run(connector.check_for_updates(date(2025, 1, 1)))
    assert [u.published for u in updates] == [date(2026, 2, 9), date(2025, 3, 20)]


def test_check_for_updates_excludes_exactly_the_pinned_date() -> None:
    """A publication ON the pinned date is already accounted for, not "newer"."""
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(SAMPLE_HTML))
    updates = run(connector.check_for_updates(date(2026, 2, 9)))
    assert updates == []


def test_check_for_updates_returns_empty_when_nothing_is_newer() -> None:
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(SAMPLE_HTML))
    updates = run(connector.check_for_updates(date(2026, 6, 1)))
    assert updates == []


def test_check_for_updates_returns_clean_titles_and_real_urls() -> None:
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(SAMPLE_HTML))
    updates = run(connector.check_for_updates(date(2020, 1, 1)))
    assert all(isinstance(u, RegulatoryUpdate) for u in updates)
    assert all("Click here" not in u.title for u in updates)
    assert all(u.url.startswith("https://www.sebi.gov.in/") for u in updates)


def test_missing_table_marker_raises_unavailable() -> None:
    connector = SebiIcdrWatchConnector(
        http_client=_FakeAsyncClient("<html><body>redesigned site</body></html>")
    )
    with pytest.raises(RegulatoryWatchUnavailable):
        run(connector.check_for_updates(date(2020, 1, 1)))


def test_http_error_raises_unavailable() -> None:
    class _FailingClient:
        async def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
            raise httpx.ConnectError("simulated DNS failure", request=httpx.Request("GET", url))

    connector = SebiIcdrWatchConnector(http_client=_FailingClient())
    with pytest.raises(RegulatoryWatchUnavailable):
        run(connector.check_for_updates(date(2020, 1, 1)))


def test_unparseable_date_row_is_skipped_not_raised() -> None:
    html = _sample_html(_row("not-a-date", "Some ICDR update", "https://www.sebi.gov.in/x.html"))
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(html))
    updates = run(connector.check_for_updates(date(2020, 1, 1)))
    assert updates == []  # skipped, not an exception


# --------------------------------------------------------------------------
# check_for_staleness — the honest-degradation orchestration
# --------------------------------------------------------------------------


def test_check_for_staleness_reports_success_and_sorts_newest_first() -> None:
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(SAMPLE_HTML))
    result = run(check_for_staleness(date(2024, 1, 1), connector=connector))
    assert isinstance(result, StalenessCheckResult)
    assert result.checked_successfully is True
    assert result.pinned_amended_through == date(2024, 1, 1)
    assert [u.published for u in result.newer_updates] == [
        date(2026, 2, 9),
        date(2025, 3, 20),
        date(2024, 11, 19),
    ]
    assert "sebi.gov.in" in result.source


def test_check_for_staleness_degrades_honestly_on_connector_failure() -> None:
    class _FailingConnector:
        async def check_for_updates(self, since: date) -> list[RegulatoryUpdate]:
            raise RegulatoryWatchUnavailable("simulated failure")

    result = run(check_for_staleness(date(2026, 3, 21), connector=_FailingConnector()))
    assert result.checked_successfully is False
    assert result.newer_updates == []
    assert result.source == "unavailable"
    # Never conflate "couldn't check" with "checked, found nothing".
    assert result.pinned_amended_through == date(2026, 3, 21)


def test_check_for_staleness_clean_when_nothing_newer_than_pin() -> None:
    connector = SebiIcdrWatchConnector(http_client=_FakeAsyncClient(SAMPLE_HTML))
    result = run(check_for_staleness(date(2026, 3, 21), connector=connector))
    assert result.checked_successfully is True
    assert result.newer_updates == []


# --------------------------------------------------------------------------
# Live network — real, self-skipping if sebi.gov.in is unreachable or has
# changed shape (same resilience pattern as test_ocr.py's Tesseract skip).
# --------------------------------------------------------------------------


def test_live_sebi_site_is_genuinely_reachable_and_parseable() -> None:
    connector = SebiIcdrWatchConnector()
    try:
        updates = run(connector.check_for_updates(date(2020, 1, 1)))
    except RegulatoryWatchUnavailable as exc:
        pytest.skip(f"sebi.gov.in unreachable or changed shape: {exc}")
    assert len(updates) > 0, "expected at least one real ICDR-tagged SEBI item since 2020"
    for update in updates:
        assert update.title
        assert update.url.startswith("https://www.sebi.gov.in")
        assert update.published > date(2020, 1, 1)
