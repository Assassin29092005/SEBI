"""Related-party transaction cross-check: entity names in rpt_summary vs. the
rest of the disclosed related-party universe."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role
from app.validate.rpt import check_rpt


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
# Named entity IS disclosed elsewhere → no findings
# --------------------------------------------------------------------------


def test_entity_disclosed_in_group_companies_produces_no_findings() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "rpt_summary",
        "Purchase of raw material aggregating Rs 78.50 lakh from Menon Farms Private Limited.",
    )
    _confirmed_fact(
        store,
        "group_companies[]",
        [{"name": "Menon Farms Private Limited", "relationship": "promoter-controlled"}],
    )
    assert check_rpt(store) == []


def test_missing_rpt_summary_yields_no_findings() -> None:
    assert check_rpt(FactStore()) == []


def test_rpt_summary_with_no_entity_mentions_is_clean() -> None:
    store = FactStore()
    _confirmed_fact(store, "rpt_summary", "Remuneration to executive directors of Rs 1.20 crore.")
    assert check_rpt(store) == []


# --------------------------------------------------------------------------
# Named entity is NOT disclosed elsewhere → material finding
# --------------------------------------------------------------------------


def test_undisclosed_entity_fires_material_finding() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "rpt_summary",
        "Office rent paid to Shadow Holdings Private Limited during the year.",
    )
    # No group_companies[]/promoter_group_entities[]/subsidiaries[]/kmp[]
    # facts name "Shadow Holdings Private Limited" anywhere.
    _confirmed_fact(
        store, "group_companies[]", [{"name": "Menon Farms Private Limited"}]
    )

    findings = check_rpt(store)

    assert [f.kind for f in findings] == ["rpt_party_not_otherwise_disclosed"]
    finding = findings[0]
    assert finding.entity_name == "Shadow Holdings Private Limited"
    assert finding.severity == "material"
    assert "(11)(A)(g)" in finding.clause_ref


def test_same_undisclosed_entity_mentioned_twice_is_deduplicated() -> None:
    store = FactStore()
    _confirmed_fact(
        store,
        "rpt_summary",
        "Rent paid to Shadow Holdings Private Limited. Shadow Holdings Private Limited also "
        "supplied packaging materials.",
    )
    findings = check_rpt(store)
    assert len(findings) == 1


def test_person_names_are_never_flagged_as_entities() -> None:
    """Only company/LLP/trust-suffixed names are extracted — no false positives on prose."""
    store = FactStore()
    _confirmed_fact(
        store,
        "rpt_summary",
        "Office rent paid to Rakesh Menon and Anita Menon jointly, approved by the Audit "
        "Committee.",
    )
    assert check_rpt(store) == []


# --------------------------------------------------------------------------
# Known related parties can come from any of the disclosed sources
# --------------------------------------------------------------------------


def test_entity_disclosed_via_promoter_group_entities_is_recognised() -> None:
    store = FactStore()
    _confirmed_fact(store, "rpt_summary", "Loan taken from Sunrise Seeds LLP.")
    _confirmed_fact(
        store, "promoter_group_entities[]", [{"name": "Sunrise Seeds LLP", "relationship": "LLP"}]
    )
    assert check_rpt(store) == []


def test_entity_disclosed_via_subsidiaries_is_recognised() -> None:
    store = FactStore()
    _confirmed_fact(
        store, "rpt_summary", "Services availed from Sunrise Agrotech Logistics Private Limited."
    )
    _confirmed_fact(
        store,
        "subsidiaries[]",
        [{"name": "Sunrise Agrotech Logistics Private Limited", "cin": "U63030MH2022PTC000000"}],
    )
    assert check_rpt(store) == []


# --------------------------------------------------------------------------
# Unconfirmed facts never feed the check
# --------------------------------------------------------------------------


def test_unconfirmed_rpt_summary_does_not_feed_the_check() -> None:
    store = FactStore()
    store.add(
        Fact(
            key="rpt_summary",
            value="Rent paid to Shadow Holdings Private Limited.",
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q:rpt_summary"),
            supplied_by=Role.PROMOTER,
        )
    )
    assert check_rpt(store) == []


# --------------------------------------------------------------------------
# The real demo fixture: RPT summary names only already-disclosed parties
# --------------------------------------------------------------------------


def test_demo_wizard_answers_have_no_rpt_findings() -> None:
    """Pin the demo baseline: the shipped wizard answers must produce zero findings."""
    wizard_path = settings.data_dir / "demo_company" / "wizard_answers.json"
    with wizard_path.open(encoding="utf-8") as fh:
        answers: dict[str, Any] = json.load(fh)

    store = FactStore()
    for key, value in answers.items():
        _confirmed_fact(store, key, value)

    assert check_rpt(store) == []
