"""Regulatory-staleness watcher.

The checklist schema pins an exact amendment date (``ChecklistHeader.
amended_through``, currently 2026-03-21 — see ``data/regulation/MANIFEST.md``)
and CLAUDE.md is explicit that any later SEBI amendment requires regenerating
the schema by hand. A demo run checks that pinned date once and moves on;
production doesn't get that luxury — ICDR amendments happen continuously,
and nothing before this module ever compared the pin against reality again
after the schema shipped.

This does **not** auto-update the schema. CLAUDE.md's own rule is that every
schema entry is human-reviewed before it ships (it's legal-adjacent
content) — this only answers "has SEBI published anything ICDR-tagged more
recently than our pin?" and routes that to a human, the same
routing-not-automating philosophy as the gap report.

``SebiIcdrWatchConnector`` is a real, working scrape of SEBI's own public
site (sebi.gov.in/sebiweb/home/HomeAction.do?doListingAll=yes&search=ICDR —
verified against the live page: a real ``<table id='sample_1'>`` of
(date, category, title+link) rows, most-recent first, genuinely filtered to
ICDR-tagged publications spanning circulars, consultation papers, informal
guidance, and enforcement notices). SEBI publishes no documented, versioned
API for this — there is no equivalent of IndianKanoon's reference client to
verify a request shape against — so this is HTML scraped from the public
site, and a future layout change could break the parser. That failure
surfaces as ``RegulatoryWatchUnavailable``, which ``check_for_staleness``
turns into an explicit ``checked_successfully=False``, never a silently
"clean" result — collapsing "the check failed" into "no updates found"
would be actively misleading about the schema's actual regulatory currency,
worse than not checking at all.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Protocol

import httpx
from pydantic import BaseModel

logger = logging.getLogger("drhp.regulatory_watch")

SEBI_SEARCH_URL = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
_SEARCH_KEYWORD = "ICDR"
_TIMEOUT_SECONDS = 30.0
_MAX_RESULTS = 20
# The results table's own id in SEBI's markup (verified live) — if this
# isn't present in the response at all, the page's layout has changed
# and nothing below can be trusted, not even "zero rows found".
_TABLE_MARKER = "id='sample_1'"
# The title-cell anchor's `title` attribute carries the full title, but also
# carries a "Click here to..." companion link's raw markup as literal text
# (SEBI nests it inside the attribute value, not as a real child tag) —
# strip that trailing boilerplate to get a clean title.
_TRAILING_ANCHOR_RE = re.compile(r"<a[^>]*>.*?</a>\s*$", re.DOTALL)


class RegulatoryUpdate(BaseModel):
    title: str
    published: date
    url: str


class StalenessCheckResult(BaseModel):
    checked_at: datetime
    pinned_amended_through: date
    # False means the live check itself could not run — network failure or
    # an unrecognised page shape. newer_updates is empty either way in that
    # case, but that emptiness is NOT a "clean" signal; see module docstring.
    checked_successfully: bool
    newer_updates: list[RegulatoryUpdate]
    source: str


class RegulatoryWatchConnector(Protocol):
    async def check_for_updates(self, since: date) -> list[RegulatoryUpdate]:
        """ICDR-tagged SEBI publications strictly newer than ``since``."""
        ...


class RegulatoryWatchUnavailable(Exception):
    """The live check could not run: network failure, non-200 response, or
    the page's structure didn't match what this parser expects.

    Callers must never treat this as "checked, found nothing" — see
    ``check_for_staleness``, which turns it into an explicit
    ``checked_successfully=False`` instead.
    """


class _ResultsTableParser(HTMLParser):
    """Extracts (date_text, title, href) rows from SEBI's results ``<tbody>``.

    A real HTML parser rather than regex: the title cell's anchor nests a
    second "Click here to comment" anchor's raw markup inside its own
    ``title`` attribute, which a naive per-tag regex would mis-split. Only
    the first anchor found per row (the title link itself) is captured —
    the guard on ``self._row_href`` ignores anything after it.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_tbody = False
        self._in_row = False
        self._cell_index = -1
        self._row_date = ""
        self._row_href = ""
        self._row_title = ""
        self.rows: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tbody":
            self._in_tbody = True
            return
        if not self._in_tbody:
            return
        if tag == "tr":
            self._in_row = True
            self._cell_index = -1
            self._row_date = ""
            self._row_href = ""
            self._row_title = ""
        elif tag == "td" and self._in_row:
            self._cell_index += 1
        elif tag == "a" and self._in_row and self._cell_index == 2 and not self._row_href:
            attrs_dict = dict(attrs)
            self._row_href = attrs_dict.get("href") or ""
            self._row_title = (attrs_dict.get("title") or "").strip()

    def handle_data(self, data: str) -> None:
        if self._in_row and self._cell_index == 0:
            self._row_date += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "tbody":
            self._in_tbody = False
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._row_date.strip() and self._row_title and self._row_href:
                self.rows.append((self._row_date.strip(), self._row_title, self._row_href))


def _clean_title(raw_title: str) -> str:
    cleaned = _TRAILING_ANCHOR_RE.sub("", raw_title).strip()
    return cleaned or raw_title


class SebiIcdrWatchConnector:
    """Real HTTP GET against SEBI's public ICDR-tagged results listing."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def _fetch_html(self) -> str:
        params = {"doListingAll": "yes", "search": _SEARCH_KEYWORD}
        try:
            if self._http_client is not None:
                response = await self._http_client.get(SEBI_SEARCH_URL, params=params)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    response = await client.get(SEBI_SEARCH_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RegulatoryWatchUnavailable(f"SEBI site request failed: {exc}") from exc
        return response.text

    async def check_for_updates(self, since: date) -> list[RegulatoryUpdate]:
        html = await self._fetch_html()
        if _TABLE_MARKER not in html:
            raise RegulatoryWatchUnavailable(
                "SEBI results page did not contain the expected results table "
                "— the site's layout may have changed"
            )
        parser = _ResultsTableParser()
        parser.feed(html)

        updates: list[RegulatoryUpdate] = []
        for date_text, title, href in parser.rows[:_MAX_RESULTS]:
            try:
                published = datetime.strptime(date_text, "%b %d, %Y").date()
            except ValueError:
                logger.warning("skipping SEBI row with unparseable date %r", date_text)
                continue
            if published <= since:
                continue
            updates.append(
                RegulatoryUpdate(title=_clean_title(title), published=published, url=href)
            )
        return updates


async def check_for_staleness(
    pinned_amended_through: date,
    connector: RegulatoryWatchConnector | None = None,
) -> StalenessCheckResult:
    """Compare SEBI's public ICDR-tagged postings against the schema's pin.

    Never raises on a connector failure — a failed live check degrades to
    ``checked_successfully=False``, exactly the honest-degradation shape
    ``app.generate.translate`` uses for the same reason: silently treating
    "couldn't check" the same as "checked, found nothing" would be worse
    than being upfront that the check didn't run.
    """
    active_connector = connector or SebiIcdrWatchConnector()
    try:
        updates = await active_connector.check_for_updates(pinned_amended_through)
    except RegulatoryWatchUnavailable as exc:
        logger.warning("regulatory staleness check unavailable: %s", exc)
        return StalenessCheckResult(
            checked_at=datetime.now(UTC),
            pinned_amended_through=pinned_amended_through,
            checked_successfully=False,
            newer_updates=[],
            source="unavailable",
        )
    return StalenessCheckResult(
        checked_at=datetime.now(UTC),
        pinned_amended_through=pinned_amended_through,
        checked_successfully=True,
        newer_updates=sorted(updates, key=lambda u: u.published, reverse=True),
        source="sebi.gov.in ICDR-tagged postings (circulars, reports, enforcement)",
    )
