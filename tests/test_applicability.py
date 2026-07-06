"""Conditional applicability: has_* conditions evaluated against confirmed facts."""

from __future__ import annotations

import asyncio

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate.sections import generate_all
from app.schema.applicability import entry_applies
from app.schema.loader import load_checklist
from app.schema.models import ChecklistEntry, Role, Severity
from app.validate.gaps import check_gaps


def _entry(applicability: str) -> ChecklistEntry:
    return ChecklistEntry(
        id="group.group_companies",
        clause_ref="ICDR Sch. VI Part A, para (13)",
        section="Group Companies",
        title="Group companies",
        description="test entry",
        applicability=applicability,
        required_facts=["group_companies[]"],
        responsible_role=Role.PROMOTER,
        severity=Severity.MATERIAL,
        output_targets=[],
    )


def _confirmed(store: FactStore, key: str, value: object) -> None:
    fact = store.add(
        Fact(
            key=key,
            value=value,
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q"),
            supplied_by=Role.PROMOTER,
        )
    )
    store.confirm(fact.fact_id)


def test_always_applies_regardless_of_store() -> None:
    assert entry_applies(_entry("always"), FactStore())


def test_has_condition_unmet_on_empty_store() -> None:
    assert not entry_applies(_entry("has_group_companies"), FactStore())


def test_has_condition_met_by_confirmed_list_fact() -> None:
    store = FactStore()
    _confirmed(store, "group_companies[]", [{"name": "Sunrise Seeds LLP"}])
    assert entry_applies(_entry("has_group_companies"), store)


def test_has_condition_not_met_by_empty_list_value() -> None:
    store = FactStore()
    _confirmed(store, "group_companies[]", [])
    assert not entry_applies(_entry("has_group_companies"), store)


def test_has_condition_ignores_unconfirmed_fact() -> None:
    store = FactStore()
    store.add(
        Fact(
            key="group_companies[]",
            value=[{"name": "X"}],
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q"),
            supplied_by=Role.PROMOTER,
        )
    )  # never confirmed
    assert not entry_applies(_entry("has_group_companies"), store)


def test_unknown_condition_treated_as_applicable() -> None:
    # Over-disclosing is safe; silently dropping a requirement is not.
    assert entry_applies(_entry("some_future_condition"), FactStore())


def test_generate_all_includes_conditional_entry_when_condition_met() -> None:
    checklist = load_checklist()
    store = FactStore()
    _confirmed(store, "group_companies[]", [{"name": "Sunrise Seeds LLP"}])
    sections = asyncio.run(generate_all(checklist, store))
    assert any(s.entry_id == "group.group_companies" for s in sections)


def test_generate_all_skips_conditional_entry_when_condition_unmet() -> None:
    checklist = load_checklist()
    sections = asyncio.run(generate_all(checklist, FactStore()))
    assert not any(s.entry_id == "group.group_companies" for s in sections)


def test_gap_report_omits_inapplicable_conditional_entry() -> None:
    checklist = load_checklist()
    report = check_gaps(checklist, FactStore())
    assert not any(g.entry_id == "group.group_companies" for g in report.gaps)
