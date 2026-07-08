"""Objects-of-the-Issue arithmetic validator: caps, residuals, contradicted inputs."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role
from app.validate.arithmetic import ArithmeticFinding, check_arithmetic


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


def _objects(*amounts_paise: int) -> list[dict[str, Any]]:
    return [
        {
            "purpose": f"Object {i}",
            "amount_paise": amount,
            "deployment_schedule": "FY2027",
        }
        for i, amount in enumerate(amounts_paise, start=1)
    ]


# --------------------------------------------------------------------------
# Clean allocation → no findings
# --------------------------------------------------------------------------


def test_clean_allocation_produces_no_findings() -> None:
    store = FactStore()
    # Rs 12.5 crore issue; objects Rs 7.7 cr + Rs 4.5 cr, GCP Rs 15 lakh.
    # Allocated 12_350_000_000; residual 150_000_000 = 1.2% ≤ 5%.
    _confirmed_fact(store, "issue_size_paise", 12_500_000_000)
    _confirmed_fact(store, "objects_of_issue[]", _objects(7_700_000_000, 4_500_000_000))
    _confirmed_fact(store, "gcp_amount_paise", 150_000_000)

    assert check_arithmetic(store) == []


# --------------------------------------------------------------------------
# Overallocation → blocker
# --------------------------------------------------------------------------


def test_overallocation_fires_blocker() -> None:
    store = FactStore()
    # Rs 10 crore issue; objects Rs 9 cr + Rs 2 cr = Rs 11 cr allocated.
    _confirmed_fact(store, "issue_size_paise", 10_000_000_000)
    _confirmed_fact(store, "objects_of_issue[]", _objects(9_000_000_000, 2_000_000_000))

    findings = check_arithmetic(store)

    over = [f for f in findings if f.kind == "objects_overallocated"]
    assert len(over) == 1
    finding = over[0]
    assert finding.severity == "blocker"
    assert finding.expected_paise == 10_000_000_000
    assert finding.actual_paise == 11_000_000_000
    assert finding.clause_ref is not None and "para (9)" in finding.clause_ref
    # Exact display figures, lakh/crore formatted.
    assert "Rs 11.00 crore" in finding.detail
    assert "Rs 10.00 crore" in finding.detail


# --------------------------------------------------------------------------
# Residual above the 5% tolerance → material
# --------------------------------------------------------------------------


def test_unallocated_residual_above_five_percent_fires_material() -> None:
    store = FactStore()
    # Rs 12.5 crore issue; only Rs 10 crore allocated → residual 20%.
    _confirmed_fact(store, "issue_size_paise", 12_500_000_000)
    _confirmed_fact(store, "objects_of_issue[]", _objects(10_000_000_000))

    findings = check_arithmetic(store)

    unallocated = [f for f in findings if f.kind == "unallocated_proceeds"]
    assert len(unallocated) == 1
    finding = unallocated[0]
    assert finding.severity == "material"
    assert finding.expected_paise == 12_500_000_000
    assert finding.actual_paise == 10_000_000_000
    assert "20.00%" in finding.detail
    assert "disclosed" in finding.detail


def test_residual_at_or_below_five_percent_is_tolerated() -> None:
    store = FactStore()
    # Residual exactly 5% (Rs 50 lakh of Rs 10 crore) must NOT fire.
    _confirmed_fact(store, "issue_size_paise", 10_000_000_000)
    _confirmed_fact(store, "objects_of_issue[]", _objects(9_500_000_000))

    assert check_arithmetic(store) == []


# --------------------------------------------------------------------------
# GCP cap: lower of 15% of issue size or Rs 10 crore
# --------------------------------------------------------------------------


def test_gcp_above_fifteen_percent_fires_blocker() -> None:
    store = FactStore()
    # Rs 10 crore issue → cap = min(15% = Rs 1.5 cr, Rs 10 cr) = Rs 1.5 crore.
    # GCP of Rs 2 crore (20%) breaches it. Objects fill the rest exactly.
    _confirmed_fact(store, "issue_size_paise", 10_000_000_000)
    _confirmed_fact(store, "objects_of_issue[]", _objects(8_000_000_000))
    _confirmed_fact(store, "gcp_amount_paise", 2_000_000_000)

    findings = check_arithmetic(store)

    breaches = [f for f in findings if f.kind == "gcp_cap_breach"]
    assert len(breaches) == 1
    finding = breaches[0]
    assert finding.severity == "blocker"
    assert finding.expected_paise == 1_500_000_000
    assert finding.actual_paise == 2_000_000_000
    assert finding.clause_ref is not None and "230(2)" in finding.clause_ref
    assert "Rs 2.00 crore" in finding.detail
    assert "Rs 1.50 crore" in finding.detail


# --------------------------------------------------------------------------
# Missing / unconfirmed inputs → one minor finding, then stop
# --------------------------------------------------------------------------


def test_missing_everything_yields_one_minor_finding_naming_both_keys() -> None:
    findings = check_arithmetic(FactStore())

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "missing_inputs"
    assert finding.severity == "minor"
    assert "issue_size_paise" in finding.detail
    assert "objects_of_issue[]" in finding.detail
    assert finding.expected_paise is None
    assert finding.actual_paise is None


def test_unconfirmed_facts_do_not_feed_the_check() -> None:
    store = FactStore()
    _confirmed_fact(store, "issue_size_paise", 10_000_000_000)
    # Added but never confirmed: must count as missing, exactly like generation.
    store.add(
        Fact(
            key="objects_of_issue[]",
            value=_objects(9_000_000_000),
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q:objects_of_issue[]"),
            supplied_by=Role.PROMOTER,
        )
    )

    findings = check_arithmetic(store)

    assert len(findings) == 1
    assert findings[0].kind == "missing_inputs"
    assert "objects_of_issue[]" in findings[0].detail
    assert "issue_size_paise" not in findings[0].detail


# --------------------------------------------------------------------------
# Contradicted issue size (the planted demo scenario) → no crash, noted
# --------------------------------------------------------------------------


def test_contradicted_issue_size_evaluates_each_value_and_notes_it() -> None:
    store = FactStore()
    # The planted contradiction: wizard says Rs 12.5 crore, the stale bank
    # sanction letter says Rs 14 crore. Both live and confirmed.
    _confirmed_fact(store, "issue_size_paise", 12_500_000_000)
    _confirmed_fact(store, "issue_size_paise", 14_000_000_000)
    # Allocation ties out against the wizard value only (residual 1.2%);
    # against Rs 14 crore, Rs 1.65 crore (11.79%) is left unallocated.
    _confirmed_fact(store, "objects_of_issue[]", _objects(7_700_000_000, 4_500_000_000))
    _confirmed_fact(store, "gcp_amount_paise", 150_000_000)

    findings = check_arithmetic(store)  # must not crash

    assert all(isinstance(f, ArithmeticFinding) for f in findings)
    unallocated = [f for f in findings if f.kind == "unallocated_proceeds"]
    assert len(unallocated) == 1
    finding = unallocated[0]
    assert finding.expected_paise == 14_000_000_000
    assert "contradicted" in finding.detail
    assert "Rs 12.50 crore" in finding.detail
    assert "Rs 14.00 crore" in finding.detail
    # No spurious findings against the value the numbers do tie out with.
    assert [f for f in findings if f.expected_paise == 12_500_000_000] == []


# --------------------------------------------------------------------------
# The real demo fixture is pinned arithmetically clean
# --------------------------------------------------------------------------


def test_demo_wizard_answers_are_arithmetically_clean() -> None:
    """Pin the demo baseline: the shipped wizard answers must produce zero findings."""
    wizard_path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, Any] = json.load(fh)

    store = FactStore()
    for key, value in answers.items():
        _confirmed_fact(store, key, value)

    assert check_arithmetic(store) == []
