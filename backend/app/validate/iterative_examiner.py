"""Iterative adversarial examiner: loops app.validate.examiner.examine(),
revising sections between rounds, until a round raises no objection that
wasn't already raised in an earlier round.

app.validate.examiner's own docstring already claims the examiner "raises
objections ... until the draft survives them" — but the single-shot
``examine()`` it defines runs exactly once and never checks whether its own
objections got addressed. This module is what actually makes that claim
true: it re-examines after every revision attempt, so "survives review"
means the draft was checked again and came back clean, not that it was
checked once and assumed clean.

Not every objection can be resolved by rewriting text. An objection is a
DATA problem — a missing fact, a cross-section contradiction, an arithmetic
mismatch, an unverified low-confidence extraction — when it names one of
these; no rewrite fixes those, only a new or corrected fact does (see
app.facts_repo.correct, app.extraction_reliability). Revision is only ever
attempted for the rest: boilerplate flags and free-text reviewer objections,
where a genuine rewrite can help. A draft whose only remaining objections are
structural still converges (the loop recognises nothing more can be done and
stops), it just does not "survive."
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore
from app.generate.sections import GeneratedSection, generate_section
from app.schema.models import Checklist
from app.validate.boilerplate import BoilerplateFlag, detect
from app.validate.contradictions import Claim, Contradiction, cross_check, extract_claims
from app.validate.examiner import ArithmeticFindingLike, Objection, examine

# Prefixes app.validate.examiner always uses for its own DATA-problem
# objections (see _deterministic_pass, _contradiction_objections,
# _arithmetic_objections, _low_confidence_objections) — an objection with one
# of these names something only a new/corrected fact can fix, never a
# rewrite. Revision is never attempted for these: it would just spend an LLM
# round-trip re-deriving text the examiner will flag again for the exact same
# reason, and the next round's re-examination would show that plainly enough
# anyway — this just skips the pointless attempt.
_STRUCTURAL_PREFIXES = (
    "Missing required fact",
    "Contradictory disclosure of",
    "Arithmetic inconsistency",
    "Low-confidence extraction cited",
)


def _is_structural(objection_text: str) -> bool:
    return any(objection_text.startswith(prefix) for prefix in _STRUCTURAL_PREFIXES)


def _objection_key(objection: Objection) -> tuple[str, str]:
    """Identity for "has this exact objection been raised before".

    clause_ref is deliberately excluded — it is derived from the checklist,
    not part of what the objection actually claims, and two rounds raising
    the same substantive point should count as the same objection even if
    clause lookup produced a different value (it never does in practice, but
    the objection's wording is the real identity, not its citation).
    """
    return (objection.entry_id, objection.objection)


class ExaminationRound(BaseModel):
    round_number: int
    objections: list[Objection]
    new_objection_count: int
    revised_entry_ids: list[str]


StopReason = Literal[
    "survived",  # this round raised zero objections
    "no_new_objections",  # objections remain, but every one was already raised
    "no_revisable_objections",  # every remaining objection is a data problem
    "max_rounds_reached",  # revision might still help; stopped early on budget
]


class IterativeExaminationReport(BaseModel):
    rounds: list[ExaminationRound]
    final_sections: list[GeneratedSection]
    final_objections: list[Objection]
    survived: bool
    stop_reason: StopReason


async def _cross_check_current(
    sections: list[GeneratedSection], store: FactStore
) -> list[Contradiction]:
    claims: list[Claim] = []
    for section in sections:
        claims.extend(await extract_claims(section, store))
    return cross_check(claims)


def _boilerplate_for(sections: list[GeneratedSection]) -> list[BoilerplateFlag]:
    flags: list[BoilerplateFlag] = []
    for section in sections:
        flags.extend(detect(section))
    return flags


async def examine_iteratively(
    sections: list[GeneratedSection],
    checklist: Checklist,
    store: FactStore,
    *,
    arithmetic_findings: Sequence[ArithmeticFindingLike] = (),
    max_rounds: int = 3,
) -> IterativeExaminationReport:
    """Re-run the examiner, revising and re-checking, until stable.

    Every round recomputes contradictions and boilerplate flags against the
    *current* sections (both depend on section text, which revision can
    change); ``arithmetic_findings`` is passed in once because it is computed
    purely from confirmed facts (app.validate.arithmetic.check_arithmetic)
    and section text cannot change it.

    Round 1 examines the sections exactly as given. Every following round
    first revises (via app.generate.sections.generate_section's ``feedback``
    parameter) every section carrying at least one non-structural objection
    from the previous round, then re-examines. Stops as soon as a round
    raises no objection unseen in an earlier round, or once every remaining
    objection is structural (see module docstring), or after ``max_rounds``.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")

    current_sections = sections
    seen_keys: set[tuple[str, str]] = set()
    rounds: list[ExaminationRound] = []
    revised_entry_ids: list[str] = []

    for round_number in range(1, max_rounds + 1):
        contradictions = await _cross_check_current(current_sections, store)
        boilerplate_flags = _boilerplate_for(current_sections)
        objections = await examine(
            current_sections,
            checklist=checklist,
            contradictions=contradictions,
            boilerplate_flags=boilerplate_flags,
            arithmetic_findings=arithmetic_findings,
            store=store,
        )

        new_objections = [o for o in objections if _objection_key(o) not in seen_keys]
        rounds.append(
            ExaminationRound(
                round_number=round_number,
                objections=objections,
                new_objection_count=len(new_objections),
                revised_entry_ids=revised_entry_ids,
            )
        )
        seen_keys.update(_objection_key(o) for o in objections)

        if not new_objections:
            return IterativeExaminationReport(
                rounds=rounds,
                final_sections=current_sections,
                final_objections=objections,
                survived=not objections,
                stop_reason="survived" if not objections else "no_new_objections",
            )

        if round_number == max_rounds:
            break

        objections_by_entry: dict[str, list[str]] = {}
        for o in objections:
            if not _is_structural(o.objection):
                objections_by_entry.setdefault(o.entry_id, []).append(o.objection)

        if not objections_by_entry:
            return IterativeExaminationReport(
                rounds=rounds,
                final_sections=current_sections,
                final_objections=objections,
                survived=False,
                stop_reason="no_revisable_objections",
            )

        entry_by_id = {entry.id: entry for entry in checklist.entries}
        revised_entry_ids = sorted(objections_by_entry)
        revised_sections: list[GeneratedSection] = []
        for section in current_sections:
            feedback = objections_by_entry.get(section.entry_id)
            entry = entry_by_id.get(section.entry_id)
            if not feedback or entry is None:
                revised_sections.append(section)
                continue
            revised_sections.append(await generate_section(entry, store, feedback=feedback))
        current_sections = revised_sections

    return IterativeExaminationReport(
        rounds=rounds,
        final_sections=current_sections,
        final_objections=rounds[-1].objections,
        survived=not rounds[-1].objections,
        stop_reason="max_rounds_reached",
    )
