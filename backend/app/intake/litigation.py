"""Litigation lookup behind a connector protocol.

Two connectors implement ``LitigationConnector``:

- ``IndianKanoonConnector`` — a real, working integration against
  api.indiankanoon.org (verified against the API's own reference client,
  ``ikapi.py``: ``POST /search/?formInput=<query>&pagenum=<n>&maxpages=<n>``,
  ``Authorization: Token <token>`` header, JSON response). It is a real,
  paid, documented service — new accounts get a signup bonus at
  api.indiankanoon.org/signup/, and non-commercial use can apply for a
  monthly free allowance. **What it can and can't tell you matters**:
  IndianKanoon indexes *published judgments* — Supreme Court, High Court,
  and tribunal decisions that have already been handed down. It has no
  concept of a currently pending trial-court case. There is no free public
  API over India's live case-status registry (eCourts/NJDG) — that remains
  a genuine, documented gap, not something this connector papers over. A
  clean result from this connector means "no published judgment found
  naming this entity," not "no litigation exists" — material litigation
  disclosure still needs the promoter's own confirmation, same as every
  other fact in this app.
- ``MockLitigationConnector`` — canned records for the synthetic demo
  company, used when no real provider is configured (``Settings.
  litigation_provider`` blank or no API token set) and as the fallback
  when the real API call fails. ``FallbackLitigationConnector`` is what
  ``app.main`` actually wires up: try the real connector if configured,
  fall back to the mock on any ``LitigationUnavailable`` — the same
  optional-real-integration-with-mandatory-fallback shape as
  ``app.llm.client``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote_plus

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("drhp.intake.litigation")

_DEMO_ENTITY_SUBSTRING = "sunrise agrotech"

INDIANKANOON_BASE_URL = "https://api.indiankanoon.org"
_TIMEOUT_SECONDS = 30.0
_RETRY_BACKOFF_SECONDS = 0.5  # module-level so tests can patch it to 0


class LitigationRecord(BaseModel):
    case_number: str
    forum: str                 # court/tribunal name
    parties: str
    nature: str                # civil / criminal / tax / regulatory
    amount_involved_paise: int | None
    status: str


class LitigationConnector(Protocol):
    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        """Search proceedings by entity name and identifiers (CIN/PAN-format)."""
        ...


class LitigationUnavailable(Exception):
    """No litigation API is usable: not configured, or the call failed.

    Callers catch this to fall back to the offline mock — mirrors
    ``app.llm.client.LLMUnavailable`` exactly.
    """


# --------------------------------------------------------------------------
# Mock connector (offline default + fallback target)
# --------------------------------------------------------------------------


def _demo_records_path() -> Path:
    return settings.data_dir / "demo_company" / "litigation_records.json"


def _load_demo_records() -> list[LitigationRecord]:
    """Load and validate canned records from the demo fixtures directory."""
    path = _demo_records_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("no demo litigation fixtures at %s", path)
        return []
    except json.JSONDecodeError as exc:
        logger.warning("invalid JSON in %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        logger.warning("expected a JSON array in %s, got %s", path, type(raw).__name__)
        return []
    return [LitigationRecord.model_validate(item) for item in raw]


class MockLitigationConnector:
    """Returns canned records for the synthetic demo company only.

    The seam matches ``LitigationConnector``: a real adapter replaces this
    class without touching any caller. Never touches the network — safe to
    run in the offline demo, and the fallback target for a real connector
    that isn't configured or isn't reachable.
    """

    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        if _DEMO_ENTITY_SUBSTRING not in entity_name.lower():
            return []
        return _load_demo_records()


# --------------------------------------------------------------------------
# Real connector: api.indiankanoon.org
# --------------------------------------------------------------------------


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, headers: dict[str, str]
) -> httpx.Response:
    """POST with a single retry on 429/5xx after a short backoff.

    IndianKanoon's search takes no body — every parameter is a query string
    on the URL (verified against their reference client) — so this posts an
    empty body, unlike app.llm.client's JSON-body variant. Any persistent
    failure is normalised to ``LitigationUnavailable`` so callers have
    exactly one exception to catch.
    """
    try:
        response = await client.post(url, headers=headers)
        if _is_retryable(response.status_code):
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise LitigationUnavailable(
            f"IndianKanoon API returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise LitigationUnavailable(f"IndianKanoon API request failed: {exc}") from exc


def _record_from_doc(doc: dict[str, Any]) -> LitigationRecord | None:
    """Map one IndianKanoon search-result document to a LitigationRecord.

    Deliberately conservative about what it claims:
    - ``amount_involved_paise`` is always ``None`` — IndianKanoon's search
      API returns a text headline, not a structured monetary field, and
      this app never derives a number from unstructured text (the same
      "never guess a figure" rule that governs LLM-generated section text).
    - ``status`` says "decided" because everything in this database is, by
      definition, a published judgment — never "pending", which this API
      cannot tell you about.
    - ``nature`` isn't classified into civil/criminal/tax/regulatory:
      IndianKanoon's search response doesn't carry that structurally, and
      guessing from keywords would be exactly the kind of confident
      wrongness this app avoids everywhere else.
    """
    tid = doc.get("tid")
    title = doc.get("title")
    if tid is None or not title:
        return None  # not enough to build a usable record — drop it, don't guess
    forum = doc.get("docsource") or "Unspecified forum"
    publishdate = doc.get("publishdate")
    status = "Decided — published judgment"
    if publishdate:
        status = f"{status} ({publishdate})"
    return LitigationRecord(
        case_number=f"indiankanoon:{tid}",
        forum=forum,
        parties=str(title),
        nature="Reported judgment",
        amount_involved_paise=None,
        status=status,
    )


class IndianKanoonConnector:
    """Real search over IndianKanoon's published-judgment database.

    One page of results per search (``maxpages=1``) — IndianKanoon bills
    per page returned, so this keeps the cost of a single lookup fixed and
    predictable at roughly the price of one search page regardless of how
    the API paginates internally. Results are additionally capped client-
    side at ``Settings.indiankanoon_max_results``.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    def _query(self, entity_name: str, identifiers: dict[str, str]) -> str:
        # Phrase-match the entity name; IndianKanoon documents phrase
        # queries via quotes. A CIN, if the caller has one, narrows the
        # search further using IndianKanoon's documented ANDD operator.
        query = f'"{entity_name}"'
        cin = identifiers.get("cin")
        if cin:
            query = f"{query} ANDD {cin}"
        return query

    async def _search_once(self, client: httpx.AsyncClient, query: str) -> dict[str, Any]:
        encoded = quote_plus(query)
        url = (
            f"{INDIANKANOON_BASE_URL}/search/?formInput={encoded}"
            f"&pagenum=0&maxpages=1"
        )
        headers = {
            "Authorization": f"Token {settings.indiankanoon_api_token}",
            "Accept": "application/json",
        }
        response = await _post_with_retry(client, url, headers)
        try:
            return response.json()
        except ValueError as exc:
            raise LitigationUnavailable(
                f"IndianKanoon API returned non-JSON response: {exc}"
            ) from exc

    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        query = self._query(entity_name, identifiers)
        if self._http_client is not None:
            data = await self._search_once(self._http_client, query)
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                data = await self._search_once(client, query)

        docs = data.get("docs")
        if not isinstance(docs, list):
            raise LitigationUnavailable(
                f"unexpected IndianKanoon response shape (no 'docs' list): {data!r}"[:300]
            )
        records: list[LitigationRecord] = []
        for doc in docs[: settings.indiankanoon_max_results]:
            if not isinstance(doc, dict):
                continue
            record = _record_from_doc(doc)
            if record is not None:
                records.append(record)
        return records


def get_litigation_connector(http_client: httpx.AsyncClient | None = None) -> LitigationConnector:
    """The configured real connector — raises if none is configured.

    Mirrors ``app.llm.client.get_provider``: exactly one place decides
    whether a real integration is usable, so adding a second real provider
    later means extending this function, not every call site.
    """
    if settings.litigation_provider == "indiankanoon" and settings.indiankanoon_api_token:
        return IndianKanoonConnector(http_client=http_client)
    raise LitigationUnavailable(
        "No litigation API configured (set LITIGATION_PROVIDER=indiankanoon and "
        "INDIANKANOON_API_TOKEN in .env); use the offline mock."
    )


class FallbackLitigationConnector:
    """Tries the configured real connector; falls back to the offline mock.

    This is what ``app.main`` actually wires up as ``AppState.
    litigation_connector`` — the app never depends on a live paid API being
    reachable, same as every other optional real integration here.
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client
        self._mock = MockLitigationConnector()

    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        try:
            connector = get_litigation_connector(http_client=self._http_client)
            return await connector.search(entity_name, identifiers)
        except LitigationUnavailable as exc:
            logger.info("litigation API unavailable (%s) — falling back to offline data", exc)
            return await self._mock.search(entity_name, identifiers)
