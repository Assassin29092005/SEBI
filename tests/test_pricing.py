"""Pricing/valuation cross-check: price-band structural rules and face-value consistency."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role
from app.validate.pricing import check_pricing


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
# Clean price band → no findings
# --------------------------------------------------------------------------


def test_clean_price_band_produces_no_findings() -> None:
    store = FactStore()
    _confirmed_fact(
        store, "price_band", {"floor_price_paise": 8500, "cap_price_paise": 9000, "face_value_paise": 1000}
    )
    assert check_pricing(store) == []


def test_missing_everything_yields_no_findings() -> None:
    assert check_pricing(FactStore()) == []


# --------------------------------------------------------------------------
# Cap below floor → invalid range, blocker
# --------------------------------------------------------------------------


def test_cap_below_floor_fires_invalid_range() -> None:
    store = FactStore()
    _confirmed_fact(
        store, "price_band", {"floor_price_paise": 9000, "cap_price_paise": 8500, "face_value_paise": 1000}
    )
    findings = check_pricing(store)
    assert [f.kind for f in findings] == ["price_band_invalid_range"]
    assert findings[0].severity == "blocker"


# --------------------------------------------------------------------------
# Cap > 120% of floor → spread exceeds cap, blocker
# --------------------------------------------------------------------------


def test_cap_above_120_percent_of_floor_fires_spread_exceeded() -> None:
    store = FactStore()
    # Floor Rs 100, cap Rs 130 -> 130% > 120% ceiling.
    _confirmed_fact(
        store,
        "price_band",
        {"floor_price_paise": 10_000, "cap_price_paise": 13_000, "face_value_paise": 1000},
    )
    findings = check_pricing(store)
    assert [f.kind for f in findings] == ["price_band_spread_exceeds_cap"]
    finding = findings[0]
    assert finding.severity == "blocker"
    assert "250(2)" in finding.clause_ref
    assert "130.00%" in finding.detail


def test_cap_at_exactly_120_percent_is_tolerated() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "price_band",
        {"floor_price_paise": 10_000, "cap_price_paise": 12_000, "face_value_paise": 1000},
    )
    assert check_pricing(store) == []


# --------------------------------------------------------------------------
# Floor below face value → blocker
# --------------------------------------------------------------------------


def test_floor_below_face_value_fires_blocker() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "price_band",
        {"floor_price_paise": 900, "cap_price_paise": 1_000, "face_value_paise": 1000},
    )
    findings = check_pricing(store)
    assert [f.kind for f in findings] == ["floor_below_face_value"]
    finding = findings[0]
    assert finding.severity == "blocker"
    assert "250(3)" in finding.clause_ref


# --------------------------------------------------------------------------
# Face value mismatch across share_allotments[] / price_band → material
# --------------------------------------------------------------------------


def test_face_value_mismatch_across_allotments_fires_material() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "share_allotments[]",
        [
            {"date": "2015-06-12", "shares": 10_000, "face_value_paise": 1000},
            {"date": "2018-08-20", "shares": 2_000_000, "face_value_paise": 500},
        ],
    )
    findings = check_pricing(store)
    assert [f.kind for f in findings] == ["face_value_mismatch"]
    assert findings[0].severity == "material"


def test_consistent_face_value_across_allotments_and_band_is_clean() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "share_allotments[]",
        [
            {"date": "2015-06-12", "shares": 10_000, "face_value_paise": 1000},
            {"date": "2018-08-20", "shares": 2_000_000, "face_value_paise": 1000},
        ],
    )
    _confirmed_fact(
        store, "price_band", {"floor_price_paise": 8500, "cap_price_paise": 9000, "face_value_paise": 1000}
    )
    assert check_pricing(store) == []


# --------------------------------------------------------------------------
# Unconfirmed facts never feed the check
# --------------------------------------------------------------------------


def test_unconfirmed_fact_does_not_feed_the_check() -> None:
    store = FactStore()
    store.add(
        Fact(
            key="price_band",
            value={"floor_price_paise": 9000, "cap_price_paise": 8500, "face_value_paise": 1000},
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q:price_band"),
            supplied_by=Role.PROMOTER,
        )
    )
    assert check_pricing(store) == []


# --------------------------------------------------------------------------
# The real demo fixture is pinned clean
# --------------------------------------------------------------------------


def test_demo_wizard_answers_have_no_pricing_findings() -> None:
    """Pin the demo baseline: the shipped wizard answers must produce zero findings."""
    wizard_path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, Any] = json.load(fh)

    store = FactStore()
    for key, value in answers.items():
        _confirmed_fact(store, key, value)

    assert check_pricing(store) == []
