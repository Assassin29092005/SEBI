"""PAN/CIN/GSTIN/DIN format validator: valid demo IDs, malformed IDs, missing fields."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role
from app.validate.identity_formats import check_identity_formats


def _confirmed_fact(store: FactStore, key: str, value: Any) -> Fact:
    fact = store.add(
        Fact(
            key=key,
            value=value,
            provenance=Provenance(kind=SourceKind.WIZARD, detail=f"q:{key}"),
            supplied_by=Role.PROMOTER,
        )
    )
    return store.confirm(fact.fact_id)


# --------------------------------------------------------------------------
# Valid IDs → no findings
# --------------------------------------------------------------------------


def test_valid_issuer_identity_produces_no_findings() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "issuer_identity",
        {"name": "Sunrise Agrotech Ltd", "cin": "U01100MH2015PLC000000", "pan": "AAACS0000A"},
    )
    assert check_identity_formats(store) == []


def test_missing_everything_yields_no_findings() -> None:
    assert check_identity_formats(FactStore()) == []


# --------------------------------------------------------------------------
# Malformed issuer PAN/CIN → blocker
# --------------------------------------------------------------------------


def test_malformed_issuer_pan_fires_blocker() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "issuer_identity",
        {"name": "Sunrise Agrotech Ltd", "cin": "U01100MH2015PLC000000", "pan": "NOTAPAN"},
    )
    findings = check_identity_formats(store)
    assert len(findings) == 1
    assert findings[0].kind == "invalid_pan"
    assert findings[0].entity == "issuer"
    assert findings[0].severity == "blocker"
    assert "AAACS0000A" in findings[0].detail  # example format shown


def test_malformed_issuer_cin_fires_blocker() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "issuer_identity",
        {"name": "Sunrise Agrotech Ltd", "cin": "U01100MH2015PLC00000", "pan": "AAACS0000A"},
    )  # 20 chars, missing one digit
    findings = check_identity_formats(store)
    assert len(findings) == 1
    assert findings[0].kind == "invalid_cin"
    assert findings[0].severity == "blocker"


def test_malformed_issuer_gstin_fires_blocker() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "issuer_identity",
        {
            "name": "Sunrise Agrotech Ltd",
            "cin": "U01100MH2015PLC000000",
            "pan": "AAACS0000A",
            "gstin": "27AAACS0000A1Z",  # missing checksum char
        },
    )
    findings = check_identity_formats(store)
    assert [f.kind for f in findings] == ["invalid_gstin"]


def test_valid_gstin_produces_no_finding() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "issuer_identity",
        {
            "name": "Sunrise Agrotech Ltd",
            "cin": "U01100MH2015PLC000000",
            "pan": "AAACS0000A",
            "gstin": "27AAACS0000A1Z5",
        },
    )
    assert check_identity_formats(store) == []


# --------------------------------------------------------------------------
# Promoter / director / subsidiary IDs
# --------------------------------------------------------------------------


def test_malformed_promoter_pan_and_din_both_fire_material() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "promoters[]",
        [{"name": "Rakesh Menon", "pan": "badpan", "din": "123"}],
    )
    findings = check_identity_formats(store)
    kinds = {f.kind for f in findings}
    assert kinds == {"invalid_pan", "invalid_din"}
    assert all(f.severity == "material" for f in findings)
    assert all("Rakesh Menon" in f.entity for f in findings)


def test_malformed_director_din_fires_material() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "board_of_directors[]",
        [{"name": "Vijay Salunkhe", "din": "abc"}],
    )
    findings = check_identity_formats(store)
    assert len(findings) == 1
    assert findings[0].kind == "invalid_din"
    assert findings[0].severity == "material"
    assert "director: Vijay Salunkhe" == findings[0].entity


def test_malformed_subsidiary_cin_fires_minor() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "subsidiaries[]",
        [{"name": "Sunrise Agrotech Logistics Private Limited", "cin": "NOTACIN"}],
    )
    findings = check_identity_formats(store)
    assert len(findings) == 1
    assert findings[0].kind == "invalid_cin"
    assert findings[0].severity == "minor"


# --------------------------------------------------------------------------
# Unconfirmed facts never feed the check
# --------------------------------------------------------------------------


def test_unconfirmed_fact_does_not_feed_the_check() -> None:
    store = FactStore()
    store.add(
        Fact(
            key="issuer_identity",
            value={"name": "Sunrise Agrotech Ltd", "pan": "NOTAPAN"},
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q:issuer_identity"),
            supplied_by=Role.PROMOTER,
        )
    )
    assert check_identity_formats(store) == []


# --------------------------------------------------------------------------
# The real demo fixture has correctly formatted IDs throughout
# --------------------------------------------------------------------------


def test_demo_wizard_answers_have_no_identity_format_findings() -> None:
    """Pin the demo baseline: the shipped wizard answers must produce zero findings."""
    wizard_path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, Any] = json.load(fh)

    store = FactStore()
    for key, value in answers.items():
        _confirmed_fact(store, key, value)

    assert check_identity_formats(store) == []
