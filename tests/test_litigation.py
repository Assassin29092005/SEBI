"""Litigation connectors: mock demo records, real IndianKanoon integration, fallback behaviour."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

import app.intake.litigation as litigation_module
from app.config import settings
from app.intake.litigation import (
    FallbackLitigationConnector,
    IndianKanoonConnector,
    LitigationRecord,
    LitigationUnavailable,
    MockLitigationConnector,
    get_litigation_connector,
)
from app.schema.loader import load_checklist
from app.schema.models import Role


def _run_search(entity: str) -> list[LitigationRecord]:
    connector = MockLitigationConnector()
    return asyncio.run(connector.search(entity, {}))


def test_search_for_demo_entity_returns_three_validated_records() -> None:
    records = _run_search("Sunrise Agrotech Ltd")

    assert len(records) == 3
    for record in records:
        assert isinstance(record, LitigationRecord)
        # every field populated per the model (nothing invented, nothing empty)
        assert record.case_number
        assert record.forum
        assert record.parties
        assert record.nature in {"civil", "criminal", "tax", "regulatory"}
        assert record.status

    # amounts, where present, are integer paise (never floats)
    for record in records:
        if record.amount_involved_paise is not None:
            assert isinstance(record.amount_involved_paise, int)
            assert record.amount_involved_paise > 0


def test_search_is_case_insensitive_on_the_entity_substring() -> None:
    lower = _run_search("sunrise agrotech ltd")
    upper = _run_search("SUNRISE AGROTECH LIMITED")
    assert len(lower) == 3
    assert len(upper) == 3


def test_search_for_unknown_entity_returns_empty_list() -> None:
    assert _run_search("Acme Widgets Ltd") == []
    assert _run_search("") == []


def test_wizard_answers_match_promoter_ontology_no_orphans() -> None:
    """Every promoter non-stub required_fact key is answered; no answers are orphaned.

    This is the contract the wizard/generator relies on: extraction and
    generation both key off the ontology, so drift here breaks the demo.
    """
    wizard_path: Path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, object] = json.load(fh)

    checklist = load_checklist()
    required: set[str] = set()
    for entry in checklist.entries:
        if not entry.stub and entry.responsible_role == Role.PROMOTER:
            required.update(entry.required_facts)

    answer_keys = set(answers.keys())
    missing = required - answer_keys
    orphan = answer_keys - required

    assert not missing, f"wizard_answers.json missing promoter facts: {sorted(missing)}"
    assert not orphan, f"wizard_answers.json has keys not in the ontology: {sorted(orphan)}"


def test_planted_contradiction_wizard_side_pins_the_expected_value() -> None:
    """The wizard side of the planted contradiction is Rs 12.5 crore in paise."""
    wizard_path: Path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, object] = json.load(fh)

    # Rs 12.5 crore = 12.5 * 1e7 rupees = 1.25e8 rupees = 1.25e10 paise
    assert answers["issue_size_paise"] == 12_500_000_000


# ---------------------------------------------------------------------------
# IndianKanoonConnector — offline (httpx.MockTransport, no network), request
# shape verified against the API's own reference client (ikapi.py).
# ---------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


def _mock_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _search_body(*docs: dict[str, Any]) -> dict[str, Any]:
    return {"found": len(docs), "docs": list(docs)}


def test_indiankanoon_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_search_body())

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search(
                "Sunrise Agrotech Ltd", {}
            )

    asyncio.run(run())

    request = captured[0]
    assert request.method == "POST"
    assert request.url.host == "api.indiankanoon.org"
    assert request.url.path == "/search/"
    assert request.headers["Authorization"] == "Token test-token"
    assert request.headers["Accept"] == "application/json"
    # phrase-quoted entity name, URL-encoded
    assert request.url.params["formInput"] == '"Sunrise Agrotech Ltd"'
    assert request.url.params["pagenum"] == "0"
    assert request.url.params["maxpages"] == "1"
    # no request body — every parameter is a query string (verified against ikapi.py)
    assert request.content in (b"", None)


def test_indiankanoon_appends_cin_with_andd_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_search_body())

    async def run() -> None:
        async with _mock_client(handler) as http:
            await IndianKanoonConnector(http_client=http).search(
                "Sunrise Agrotech Ltd", {"cin": "U01100MH2020PLC123456"}
            )

    asyncio.run(run())
    assert captured[0].url.params["formInput"] == (
        '"Sunrise Agrotech Ltd" ANDD U01100MH2020PLC123456'
    )


def test_indiankanoon_parses_docs_into_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_search_body(
                {
                    "tid": 123456,
                    "title": "Sunrise Agrotech Ltd vs XYZ Traders on 12 March, 2024",
                    "docsource": "Bombay High Court",
                    "publishdate": "2024-03-12",
                    "headline": "...breach of contract...",
                }
            ),
        )

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search(
                "Sunrise Agrotech Ltd", {}
            )

    records = asyncio.run(run())
    assert len(records) == 1
    record = records[0]
    assert record.case_number == "indiankanoon:123456"
    assert record.forum == "Bombay High Court"
    assert record.parties == "Sunrise Agrotech Ltd vs XYZ Traders on 12 March, 2024"
    assert record.status == "Decided — published judgment (2024-03-12)"
    # never guessed from unstructured text — see _record_from_doc's docstring
    assert record.amount_involved_paise is None


def test_indiankanoon_drops_docs_missing_tid_or_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_search_body(
                {"tid": 1, "title": "Has both"},
                {"title": "Missing tid"},
                {"tid": 2},  # missing title
                "not even a dict",  # type: ignore[arg-type]
            ),
        )

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search("Anything", {})

    records = asyncio.run(run())
    assert len(records) == 1
    assert records[0].case_number == "indiankanoon:1"


def test_indiankanoon_respects_max_results_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")
    monkeypatch.setattr(settings, "indiankanoon_max_results", 2)

    docs = [{"tid": i, "title": f"Case {i}"} for i in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_search_body(*docs))

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search("Anything", {})

    assert len(asyncio.run(run())) == 2


def test_indiankanoon_raises_on_missing_docs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errmsg": "something went sideways"})

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search("Anything", {})

    with pytest.raises(LitigationUnavailable):
        asyncio.run(run())


def test_indiankanoon_retries_once_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")
    monkeypatch.setattr(litigation_module, "_RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"errmsg": "rate limited"})
        return httpx.Response(200, json=_search_body({"tid": 1, "title": "After retry"}))

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search("Anything", {})

    records = asyncio.run(run())
    assert len(calls) == 2
    assert records[0].case_number == "indiankanoon:1"


def test_indiankanoon_gives_up_after_one_retry_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")
    monkeypatch.setattr(litigation_module, "_RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={"errmsg": "down"})

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search("Anything", {})

    with pytest.raises(LitigationUnavailable):
        asyncio.run(run())
    assert len(calls) == 2  # original attempt + exactly one retry


def test_indiankanoon_no_retry_on_plain_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "indiankanoon_api_token", "bad-token")
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"errmsg": "invalid token"})

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await IndianKanoonConnector(http_client=http).search("Anything", {})

    with pytest.raises(LitigationUnavailable):
        asyncio.run(run())
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# get_litigation_connector — the same "one place decides" pattern as
# app.llm.client.get_provider
# ---------------------------------------------------------------------------


def test_get_litigation_connector_raises_when_provider_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "litigation_provider", "")
    monkeypatch.setattr(settings, "indiankanoon_api_token", "some-token")
    with pytest.raises(LitigationUnavailable):
        get_litigation_connector()


def test_get_litigation_connector_raises_when_token_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "litigation_provider", "indiankanoon")
    monkeypatch.setattr(settings, "indiankanoon_api_token", "")
    with pytest.raises(LitigationUnavailable):
        get_litigation_connector()


def test_get_litigation_connector_returns_indiankanoon_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "litigation_provider", "indiankanoon")
    monkeypatch.setattr(settings, "indiankanoon_api_token", "some-token")
    assert isinstance(get_litigation_connector(), IndianKanoonConnector)


# ---------------------------------------------------------------------------
# FallbackLitigationConnector — real when configured+working, mock otherwise
# ---------------------------------------------------------------------------


def test_fallback_uses_mock_when_no_real_provider_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "litigation_provider", "")
    records = asyncio.run(FallbackLitigationConnector().search("Sunrise Agrotech Ltd", {}))
    assert len(records) == 3  # the demo fixtures, via the mock


def test_fallback_uses_real_connector_when_it_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "litigation_provider", "indiankanoon")
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_search_body({"tid": 999, "title": "Real API record"})
        )

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await FallbackLitigationConnector(http_client=http).search(
                "Sunrise Agrotech Ltd", {}
            )

    records = asyncio.run(run())
    assert len(records) == 1
    assert records[0].case_number == "indiankanoon:999"  # real data, not the 3-record mock


def test_fallback_falls_back_to_mock_when_real_connector_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "litigation_provider", "indiankanoon")
    monkeypatch.setattr(settings, "indiankanoon_api_token", "test-token")
    monkeypatch.setattr(litigation_module, "_RETRY_BACKOFF_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errmsg": "down"})

    async def run() -> list[LitigationRecord]:
        async with _mock_client(handler) as http:
            return await FallbackLitigationConnector(http_client=http).search(
                "Sunrise Agrotech Ltd", {}
            )

    records = asyncio.run(run())
    assert len(records) == 3  # fell back to the demo fixtures, not an empty/broken result
