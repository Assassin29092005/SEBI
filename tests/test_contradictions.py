"""Contradiction check: normalisation, cross-section conflicts, uncited monetary claims."""

from __future__ import annotations

import asyncio
from typing import Any

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate.sections import Citation, GeneratedSection
from app.schema.models import Role
from app.validate.contradictions import (
    Claim,
    cross_check,
    extract_claims,
    normalize,
)


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


def _section_citing(fact: Fact, *, entry_id: str, text: str) -> GeneratedSection:
    # Cite the entire text as the fact's supporting span.
    return GeneratedSection(
        entry_id=entry_id,
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id=fact.fact_id, text_span=(0, len(text)))],
        missing_facts=[],
    )


def run(coro: Any) -> Any:  # noqa: ANN401 — asyncio bridge
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Cross-section conflict on the same subject → one Contradiction
# --------------------------------------------------------------------------


def test_two_sections_disagree_on_issue_size_produces_one_contradiction() -> None:
    store = FactStore()
    # 12.5 crore in paise: 12.5 * 10^9 = 12_500_000_000
    # 14   crore in paise: 14   * 10^9 = 14_000_000_000
    fact_a = _confirmed_fact(store, "issue_size_paise", 12_500_000_000)
    fact_b = _confirmed_fact(store, "issue_size_paise", 14_000_000_000)

    section_a = _section_citing(
        fact_a, entry_id="general.cover_pages", text="The issue size is 12,50,00,00,000 paise."
    )
    section_b = _section_citing(
        fact_b, entry_id="objects.use_of_proceeds", text="The issue size is 14,00,00,00,000 paise."
    )

    all_claims: list[Claim] = []
    for section in (section_a, section_b):
        all_claims.extend(run(extract_claims(section, store)))

    contradictions = cross_check(all_claims)

    on_issue_size = [c for c in contradictions if c.subject == "issue_size_paise"]
    assert len(on_issue_size) == 1
    # Both original claims must be part of the contradiction group.
    grouped_values = {c.value for c in on_issue_size[0].claims}
    assert grouped_values == {"12500000000", "14000000000"}


# --------------------------------------------------------------------------
# Consistent values → no contradictions on that subject.
# --------------------------------------------------------------------------


def test_two_sections_agree_on_issue_size_yields_no_contradiction() -> None:
    store = FactStore()
    fact_a = _confirmed_fact(store, "issue_size_paise", 14_000_000_000)
    fact_b = _confirmed_fact(store, "issue_size_paise", 14_000_000_000)

    # Two different fact ids but the same numeric value.
    section_a = _section_citing(
        fact_a, entry_id="general.cover_pages", text="The issue size is 14 crore."
    )
    section_b = _section_citing(
        fact_b, entry_id="objects.use_of_proceeds", text="The issue size is 14 crore."
    )

    all_claims: list[Claim] = []
    for section in (section_a, section_b):
        all_claims.extend(run(extract_claims(section, store)))

    contradictions = cross_check(all_claims)

    assert [c for c in contradictions if c.subject == "issue_size_paise"] == []


# --------------------------------------------------------------------------
# normalize() collapses surface forms to a single canonical string
# --------------------------------------------------------------------------


def test_normalize_collapses_three_surface_forms_of_same_amount() -> None:
    # Three surface forms of the SAME amount = ₹14 crore = 14 * 10^7 rupees
    # = 14 * 10^9 paise = "14000000000".
    #
    # - "₹14 crore"        → currency marker + crore unit  → 14 * 10^9 paise
    # - "Rs. 14,00,00,000" → Rs. + rupees-with-grouping     → 14_00_00_000 * 100 paise
    # - "₹14,00,00,000"    → ₹  + rupees-with-grouping     → 14_00_00_000 * 100 paise
    # (The docstring on normalize explicitly documents both the crore form and
    # the "Rs. 14,00,00,000" form collapsing to the same paise integer.)
    forms = ["₹14 crore", "Rs. 14,00,00,000", "₹14,00,00,000"]
    normalised = {normalize(form) for form in forms}
    assert normalised == {"14000000000"}

    # Now three CLAIMS at the same subject: the three equivalent money forms
    # collapse to one canonical value; adding a *fourth* dissenting bare-paise
    # integer produces a contradiction (distinct normalised count = 2).
    claims = [
        Claim(
            section_entry_id="a",
            kind="number",
            subject="issue_size_paise",
            value=forms[0],
            text_span=(0, len(forms[0])),
        ),
        Claim(
            section_entry_id="b",
            kind="number",
            subject="issue_size_paise",
            value=forms[1],
            text_span=(0, len(forms[1])),
        ),
        Claim(
            section_entry_id="c",
            kind="number",
            subject="issue_size_paise",
            value=forms[2],
            text_span=(0, len(forms[2])),
        ),
    ]
    # Three matching forms → no contradiction (they all normalise the same).
    assert cross_check(claims) == []

    # Add a dissenting value; now we have a contradiction.
    claims.append(
        Claim(
            section_entry_id="d",
            kind="number",
            subject="issue_size_paise",
            value="99999999999",  # a different value entirely
            text_span=(0, 11),
        )
    )
    contradictions = cross_check(claims)
    assert len(contradictions) == 1
    assert contradictions[0].subject == "issue_size_paise"
    # The contradiction group carries all four claims.
    assert len(contradictions[0].claims) == 4


def test_normalize_strips_thousands_grouping_from_plain_numbers() -> None:
    assert normalize("1,234") == "1234"
    assert normalize("1,00,000") == "100000"


def test_normalize_falls_through_to_casefold_on_non_numeric_text() -> None:
    assert normalize("  Sunrise  Agrotech  ") == "sunrise agrotech"


# --------------------------------------------------------------------------
# Uncited monetary expressions get subject prefix "uncited:<entry_id>"
# --------------------------------------------------------------------------


def test_uncited_monetary_expression_produces_uncited_subject_claim() -> None:
    # Section has no citations; a monetary expression sits in the free text.
    section = GeneratedSection(
        entry_id="general.cover_pages",
        section="General",
        text="The issue size is ₹14 crore, split across two tranches.",
        citations=[],
        missing_facts=[],
    )

    claims = run(extract_claims(section))

    money_claims = [c for c in claims if c.value.strip().startswith("₹")]
    assert len(money_claims) == 1
    assert money_claims[0].subject == "uncited:general.cover_pages"
    assert money_claims[0].kind == "number"
    # The span points into the section text at the money expression.
    start, end = money_claims[0].text_span
    assert section.text[start:end] == money_claims[0].value
