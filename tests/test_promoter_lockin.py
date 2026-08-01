"""Promoter lock-in validator: minimum promoters' contribution (MPC) and the
mandatory 3-year/2-year/1-year lock-in bifurcation."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role
from app.validate.promoter_lockin import check_promoter_lockin


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


def _allotments(*shares: int) -> list[dict[str, Any]]:
    return [{"date": f"2020-01-0{i}", "shares": s} for i, s in enumerate(shares, start=1)]


# --------------------------------------------------------------------------
# Missing inputs → no findings (gap checker's job)
# --------------------------------------------------------------------------


def test_missing_everything_yields_no_findings() -> None:
    assert check_promoter_lockin(FactStore()) == []


# --------------------------------------------------------------------------
# Comfortable promoter holding, flat lock-in disclosed → bifurcation finding
# --------------------------------------------------------------------------


def test_flat_lockin_with_excess_holding_fires_bifurcation_finding() -> None:
    store = FactStore()
    # 10,000,000 pre-issue shares; promoters hold 6,300,000 (63%) — comfortably
    # above the 20% MPC even after floor-price dilution, so most of it is
    # "excess" that Reg 238(b) requires split 2y/1y, not left flat at 3y.
    _confirmed_fact(store, "share_allotments[]", _allotments(10_000_000))
    _confirmed_fact(
        store,
        "promoter_shareholding[]",
        [
            {"promoter": "Rakesh Menon", "shares_held_pre_issue": 4_200_000, "lock_in_years": 3},
            {"promoter": "Anita Menon", "shares_held_pre_issue": 2_100_000, "lock_in_years": 3},
        ],
    )
    _confirmed_fact(store, "issue_size_paise", 12_500_000_000)
    _confirmed_fact(
        store, "price_band", {"floor_price_paise": 8500, "cap_price_paise": 9000, "face_value_paise": 1000}
    )

    findings = check_promoter_lockin(store)

    assert [f.kind for f in findings] == ["lockin_bifurcation_missing"]
    finding = findings[0]
    assert finding.severity == "material"
    assert "238" in finding.clause_ref
    assert "Rakesh Menon" in finding.detail
    assert "Anita Menon" in finding.detail


# --------------------------------------------------------------------------
# MPC shortfall → blocker
# --------------------------------------------------------------------------


def test_promoter_holding_below_twenty_percent_fires_mpc_shortfall() -> None:
    store = FactStore()
    # 10,000,000 pre-issue shares; promoters hold only 1,000,000 (10%) —
    # below the 20% minimum promoters' contribution even before dilution.
    _confirmed_fact(store, "share_allotments[]", _allotments(10_000_000))
    _confirmed_fact(
        store,
        "promoter_shareholding[]",
        [{"promoter": "Rakesh Menon", "shares_held_pre_issue": 1_000_000, "lock_in_years": 3}],
    )
    _confirmed_fact(store, "issue_size_paise", 12_500_000_000)
    _confirmed_fact(
        store, "price_band", {"floor_price_paise": 8500, "cap_price_paise": 9000, "face_value_paise": 1000}
    )

    findings = check_promoter_lockin(store)

    assert [f.kind for f in findings] == ["mpc_shortfall"]
    finding = findings[0]
    assert finding.severity == "blocker"
    assert "236" in finding.clause_ref


# --------------------------------------------------------------------------
# Promoter holding exactly at/below their pro-rata MPC share → clean
# --------------------------------------------------------------------------


def test_promoter_holding_within_mpc_share_produces_no_findings() -> None:
    store = FactStore()
    # Tiny issue size relative to pre-issue capital keeps dilution negligible;
    # promoters hold exactly the MPC share computed here (20% of post-issue),
    # so there's no excess to bifurcate and no shortfall either.
    _confirmed_fact(store, "share_allotments[]", _allotments(10_000_000))
    _confirmed_fact(store, "issue_size_paise", 1)  # ~0 new shares at any floor price
    _confirmed_fact(
        store, "price_band", {"floor_price_paise": 8500, "cap_price_paise": 9000, "face_value_paise": 1000}
    )
    # 20% of ~10,000,000 post-issue shares = 2,000,000
    _confirmed_fact(
        store,
        "promoter_shareholding[]",
        [{"promoter": "Rakesh Menon", "shares_held_pre_issue": 2_000_000, "lock_in_years": 3}],
    )

    assert check_promoter_lockin(store) == []


# --------------------------------------------------------------------------
# The real demo fixture: comfortable promoter holding, bifurcation expected
# --------------------------------------------------------------------------


def test_demo_wizard_answers_flag_missing_lockin_bifurcation() -> None:
    """The demo's flat lock_in_years=3 per promoter is exactly the gap this
    check exists to catch — pinning it documents the intended behaviour."""
    wizard_path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, Any] = json.load(fh)

    store = FactStore()
    for key, value in answers.items():
        _confirmed_fact(store, key, value)

    findings = check_promoter_lockin(store)
    assert [f.kind for f in findings] == ["lockin_bifurcation_missing"]
