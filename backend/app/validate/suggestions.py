"""Auto-suggested fixes: turns validator OUTPUT into a concrete next action,
without ever inventing a fact, a number, or a name.

The gap report already routes a missing fact to the right role; the
examiner already raises objections. Neither says what to actually DO about
a specific finding beyond "go supply this." This module adds that, but only
where a suggestion can be computed purely from data the validators already
produced — no LLM, no new fact:

- **Arithmetic** (`app.validate.arithmetic`): every ``ArithmeticFinding``
  already carries ``expected_paise``/``actual_paise`` — the reconciling
  amount is `abs(expected - actual)`, arithmetic on numbers already in the
  finding, not a new one.
- **Low-confidence extractions** (the same detection
  ``app.validate.examiner._low_confidence_objections`` runs, computed
  independently here so the suggestion can carry the fact's
  ``document_id``/``page`` for a direct "jump to source" action — the
  examiner's own objection text has nowhere to put that, it's prose).
- **Boilerplate** (`app.validate.boilerplate`): points at a capability that
  already exists — the iterative examiner
  (`app.validate.iterative_examiner`) can genuinely revise this kind of
  prose — rather than leaving "generic/boilerplate disclosure" as a dead
  end with no suggested next step.

Deliberately NOT covered: missing-fact gaps already get a routed action
(the gap report + wizard deep-link); a contradiction has no single
"correct" reconciling value to suggest — the promoter/auditor/banker has
to say which confirmed value is right, and inventing a preference here
would be exactly the kind of confident wrongness this app avoids
everywhere else.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore, SourceKind
from app.generate.sections import GeneratedSection
from app.validate.arithmetic import ArithmeticFinding
from app.validate.boilerplate import BoilerplateFlag

# Mirrors app.validate.examiner._LOW_CONFIDENCE_THRESHOLD exactly (same
# reasoning as app.extraction_reliability's own copy of this constant) —
# one definition of "low confidence, flag for review," not a second one.
_LOW_CONFIDENCE_THRESHOLD = 0.7

SuggestionCategory = Literal["arithmetic", "low_confidence_extraction", "boilerplate"]


class SuggestedFix(BaseModel):
    entry_id: str
    category: SuggestionCategory
    message: str
    # "Jump to source" target for low_confidence_extraction; None otherwise.
    fact_id: str | None = None
    document_id: str | None = None
    page: int | None = None


def _suggest_arithmetic_fixes(
    findings: Sequence[ArithmeticFinding], use_of_proceeds_entry_id: str
) -> list[SuggestedFix]:
    """One suggestion per finding, routed to the same entry the examiner
    routes its own arithmetic objections to (see
    app.validate.examiner._arithmetic_objections) — objects.use_of_proceeds
    when the checklist has it, otherwise a synthetic id the frontend can
    still group by."""
    suggestions: list[SuggestedFix] = []
    for finding in findings:
        if finding.expected_paise is None or finding.actual_paise is None:
            continue  # nothing to compute a reconciling amount from
        gap_paise = abs(finding.expected_paise - finding.actual_paise)
        if finding.kind == "objects_overallocated":
            message = (
                f"Objects of the issue (including GCP) exceed the issue size by "
                f"{gap_paise} paise — reduce an object's amount (or GCP), or "
                "increase the issue size, by that much to reconcile."
            )
        elif finding.kind == "unallocated_proceeds":
            message = (
                f"{gap_paise} paise of the issue is not allocated to any object — "
                "allocate it to an existing or new object, or disclose it as "
                "issue expenses."
            )
        elif finding.kind == "gcp_cap_breach":
            message = (
                f"General corporate purposes exceeds the regulatory cap by "
                f"{gap_paise} paise — reduce GCP to at most {finding.expected_paise} "
                "paise to comply with Reg. 230(2)."
            )
        else:  # pragma: no cover - defensive: a future FindingKind with no template yet
            continue
        suggestions.append(
            SuggestedFix(
                entry_id=use_of_proceeds_entry_id, category="arithmetic", message=message
            )
        )
    return suggestions


def _suggest_low_confidence_fixes(
    sections: list[GeneratedSection], store: FactStore
) -> list[SuggestedFix]:
    """Same detection as app.validate.examiner._low_confidence_objections
    (distinct, low-confidence, document-sourced facts actually cited in the
    draft) — computed independently here so the suggestion can carry a
    direct jump-to-source target the examiner's prose objection has no slot
    for."""
    suggestions: list[SuggestedFix] = []
    seen: set[str] = set()
    for section in sections:
        for citation in section.citations:
            if citation.fact_id in seen:
                continue
            seen.add(citation.fact_id)
            try:
                fact = store.get(citation.fact_id)
            except KeyError:
                continue
            if fact.provenance.kind != SourceKind.DOCUMENT:
                continue
            if fact.confidence >= _LOW_CONFIDENCE_THRESHOLD:
                continue
            suggestions.append(
                SuggestedFix(
                    entry_id=section.entry_id,
                    category="low_confidence_extraction",
                    message=(
                        f"'{fact.key}' was extracted at confidence {fact.confidence:.2f} "
                        f"from {fact.provenance.detail} — re-open the source and confirm "
                        "the value is read correctly before relying on it."
                    ),
                    fact_id=fact.fact_id,
                    document_id=fact.provenance.document_id,
                    page=fact.provenance.page,
                )
            )
    return suggestions


def _suggest_boilerplate_fixes(flags: Sequence[BoilerplateFlag]) -> list[SuggestedFix]:
    """One suggestion per section carrying at least one flag — pointing at
    the iterative examiner, which can genuinely revise boilerplate prose
    (see app.validate.iterative_examiner), not a dead-end objection."""
    flagged_entries = sorted({flag.entry_id for flag in flags})
    return [
        SuggestedFix(
            entry_id=entry_id,
            category="boilerplate",
            message=(
                "This section has generic/boilerplate phrasing flagged. "
                "Run the iterative examiner — it can revise wording like this "
                "automatically and re-check until it's clean."
            ),
        )
        for entry_id in flagged_entries
    ]


def compute_suggested_fixes(
    sections: list[GeneratedSection],
    store: FactStore,
    arithmetic_findings: Sequence[ArithmeticFinding],
    boilerplate_flags: Sequence[BoilerplateFlag],
    use_of_proceeds_entry_id: str = "objects.use_of_proceeds",
) -> list[SuggestedFix]:
    """All auto-suggested fixes across the three covered categories, in a
    fixed order: arithmetic, low-confidence extraction, boilerplate."""
    return [
        *_suggest_arithmetic_fixes(arithmetic_findings, use_of_proceeds_entry_id),
        *_suggest_low_confidence_fixes(sections, store),
        *_suggest_boilerplate_fixes(boilerplate_flags),
    ]
