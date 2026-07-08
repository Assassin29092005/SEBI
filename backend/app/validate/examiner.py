"""Adversarial examiner agent — plays exchange reviewer against the draft.

CUT-LINE FEATURE (day 10–11, first in cut priority): raises objections
section-by-section until the draft survives them.

Two passes:

1. **Deterministic pass** (always runs, fully offline): per section, one
   objection for every unresolved ``[REQUIRES INPUT]`` fact (naming the fact
   key and who must supply it), plus an "uncited quantitative claim"
   objection when the section text contains digit sequences but carries zero
   citations. When the caller feeds in outputs from the other validators, the
   pass also raises substantive cross-check objections — in this fixed order
   after the objections above: one per ``Contradiction`` (naming the subject
   and the disagreeing values), one per arithmetic finding (routed to
   ``objects.use_of_proceeds`` when the checklist has that entry), one per
   ``BoilerplateFlag`` (quoting the flagged span), and one per distinct
   low-confidence document-extracted fact cited by a section.
2. **LLM pass** via ``app.llm.client.grounded_complete`` (temperature 0)
   when a provider is available: plays an SME-exchange reviewer raising
   specific objections per section, returning strict JSON. Returned
   ``clause_ref`` values are validated against the checklist — the examiner
   never invents clause citations; anything that does not exactly match the
   section's checklist clause is sanitised to ``None``. When the LLM is
   unavailable (offline demo, no API key), the pass is skipped silently.

Objections are created with ``resolved=False``; resolution happens in the
banker review workflow (``app.review``), not here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from app.facts import FactStore, SourceKind
from app.generate.sections import GeneratedSection
from app.llm import client as llm_client
from app.schema.models import Checklist
from app.validate.boilerplate import BoilerplateFlag
from app.validate.contradictions import Contradiction

logger = logging.getLogger("drhp.examiner")

try:
    from app.llm.client import LLMUnavailable
except ImportError:  # pragma: no cover — until app.llm.client exposes it

    class LLMUnavailable(Exception):  # type: ignore[no-redef]
        """Local stand-in until ``app.llm.client`` defines LLMUnavailable."""


#: Errors that mean "no LLM right now" — the LLM pass skips silently on these.
#: NotImplementedError covers the current provider stubs (offline-first demo).
_LLM_SKIP_ERRORS: tuple[type[Exception], ...] = (LLMUnavailable, NotImplementedError)

# Matches the marker produced by app.generate.sections.requires_input_marker:
#   [REQUIRES INPUT: <fact_key> — <role> can provide this]
_MARKER_RE = re.compile(r"\[REQUIRES INPUT:\s*(?P<fact>[^\]]+?)\s*—\s*(?P<who>[^\]]+?)\s*\]")
_PROVIDER_SUFFIX = " can provide this"
_DIGIT_RE = re.compile(r"\d")
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

_EXAMINER_SYSTEM = (
    "You are a listing reviewer at an Indian SME exchange (BSE SME / NSE Emerge) examining "
    "draft DRHP sections prepared under SEBI ICDR Chapter IX. For each section, raise the "
    "specific, actionable objections an exchange reviewer would send back — vague, "
    "boilerplate, internally inconsistent, or insufficiently disclosed content. Respond with "
    "STRICT JSON only: a JSON array of objects, each "
    '{"entry_id": <string>, "objection": <string>, "clause_ref": <string or null>}. '
    "entry_id must be one of the provided section entry_ids. Never invent clause "
    "references: cite a clause_ref only if it was explicitly provided for that section, "
    "otherwise use null. Output the JSON array only — no prose, no markdown fences."
)


# Arithmetic findings route here when the checklist carries this entry —
# use-of-proceeds is where deployment-schedule sums live.
_USE_OF_PROCEEDS_ENTRY = "objects.use_of_proceeds"

# Document-extracted facts cited below this confidence get a re-verify objection.
_LOW_CONFIDENCE_THRESHOLD = 0.7


class ArithmeticFindingLike(Protocol):
    """Duck-typed view of ``app.validate.arithmetic.ArithmeticFinding``.

    A ``Protocol`` instead of an import keeps the examiner free of a hard
    dependency on the arithmetic module (which may not exist in every build):
    any object carrying these attributes is accepted.
    """

    kind: str
    detail: str
    clause_ref: str | None


class Objection(BaseModel):
    entry_id: str
    objection: str
    clause_ref: str | None
    resolved: bool = False


def _supplier_from_marker(who: str) -> str:
    """Strip the trailing ' can provide this' from a marker's provider clause."""
    if who.endswith(_PROVIDER_SUFFIX):
        return who[: -len(_PROVIDER_SUFFIX)].strip()
    return who


def _deterministic_pass(
    sections: list[GeneratedSection],
    clause_by_entry: dict[str, str],
    role_by_entry: dict[str, str],
) -> list[Objection]:
    """Offline objections: unresolved [REQUIRES INPUT] facts and uncited numbers."""
    objections: list[Objection] = []
    for section in sections:
        clause_ref = clause_by_entry.get(section.entry_id)

        # Who can supply each fact, read off the markers in the text itself.
        marker_suppliers = {
            match.group("fact").strip(): _supplier_from_marker(match.group("who").strip())
            for match in _MARKER_RE.finditer(section.text)
        }

        seen: set[str] = set()
        for fact_key in [*section.missing_facts, *marker_suppliers]:
            if fact_key in seen:
                continue
            seen.add(fact_key)
            supplier = (
                marker_suppliers.get(fact_key)
                or role_by_entry.get(section.entry_id)
                or "the responsible party"
            )
            objections.append(
                Objection(
                    entry_id=section.entry_id,
                    objection=(
                        f"Missing required fact '{fact_key}': the draft still carries an "
                        f"unresolved [REQUIRES INPUT] marker — {supplier} must supply it "
                        "before this section can be reviewed."
                    ),
                    clause_ref=clause_ref,
                )
            )

        # Digits inside [REQUIRES INPUT] markers (e.g. fact keys like
        # fy2024_revenue) are not quantitative claims — strip markers first.
        prose = _MARKER_RE.sub("", section.text)
        if _DIGIT_RE.search(prose) and not section.citations:
            objections.append(
                Objection(
                    entry_id=section.entry_id,
                    objection=(
                        "Uncited quantitative claim: the section states numeric figures but "
                        "carries no citations back to confirmed facts in the fact store."
                    ),
                    clause_ref=clause_ref,
                )
            )
    return objections


def _contradiction_objections(
    contradictions: Sequence[Contradiction],
    clause_by_entry: dict[str, str],
) -> list[Objection]:
    """One objection per contradiction, anchored to the first claim's section."""
    objections: list[Objection] = []
    for contradiction in contradictions:
        if not contradiction.claims:
            continue  # nothing to anchor the objection to
        entry_id = contradiction.claims[0].section_entry_id
        stated = " vs. ".join(
            f"'{claim.value}' (in {claim.section_entry_id})" for claim in contradiction.claims
        )
        objections.append(
            Objection(
                entry_id=entry_id,
                objection=(
                    f"Contradictory disclosure of '{contradiction.subject}': the draft states "
                    f"{stated}. These values disagree — reconcile them before filing."
                ),
                clause_ref=clause_by_entry.get(entry_id),
            )
        )
    return objections


def _arithmetic_objections(
    findings: Sequence[ArithmeticFindingLike],
    sections: list[GeneratedSection],
    checklist_entry_ids: set[str],
) -> list[Objection]:
    """One objection per arithmetic finding, routed to the use-of-proceeds entry.

    clause_ref discipline: the clause arrives on the finding itself (the
    arithmetic checker derives it from the checklist) — never invented here.
    """
    if _USE_OF_PROCEEDS_ENTRY in checklist_entry_ids:
        entry_id = _USE_OF_PROCEEDS_ENTRY
    elif sections:
        entry_id = sections[0].entry_id
    else:
        entry_id = _USE_OF_PROCEEDS_ENTRY  # last resort — never drop a finding

    objections: list[Objection] = []
    for finding in findings:
        clause_ref = finding.clause_ref
        objections.append(
            Objection(
                entry_id=entry_id,
                objection=(
                    f"Arithmetic inconsistency ({finding.kind}): {finding.detail} — "
                    "the figures must reconcile before filing."
                ),
                clause_ref=clause_ref if isinstance(clause_ref, str) else None,
            )
        )
    return objections


def _boilerplate_objections(
    flags: Sequence[BoilerplateFlag],
    sections: list[GeneratedSection],
    clause_by_entry: dict[str, str],
) -> list[Objection]:
    """One objection per flag, quoting the flagged span from the section text."""
    text_by_entry: dict[str, str] = {}
    for section in sections:
        text_by_entry.setdefault(section.entry_id, section.text)

    objections: list[Objection] = []
    for flag in flags:
        start, end = flag.text_span
        quote = text_by_entry.get(flag.entry_id, "")[start:end]
        subject = f'"{quote}" ({flag.reason})' if quote else f"Flagged text ({flag.reason})"
        objections.append(
            Objection(
                entry_id=flag.entry_id,
                objection=(
                    f"{subject} — generic/boilerplate disclosure — replace with "
                    "issuer-specific language."
                ),
                clause_ref=clause_by_entry.get(flag.entry_id),
            )
        )
    return objections


def _low_confidence_objections(
    sections: list[GeneratedSection],
    store: FactStore,
    clause_by_entry: dict[str, str],
) -> list[Objection]:
    """Re-verify objections for cited document-extracted facts below threshold.

    One objection per distinct fact_id, anchored to the first section citing it.
    """
    objections: list[Objection] = []
    seen: set[str] = set()
    for section in sections:
        for citation in section.citations:
            if citation.fact_id in seen:
                continue
            seen.add(citation.fact_id)
            try:
                fact = store.get(citation.fact_id)
            except KeyError:
                continue  # dangling citation — the gap checker's concern, not ours
            if fact.provenance.kind != SourceKind.DOCUMENT:
                continue
            if fact.confidence >= _LOW_CONFIDENCE_THRESHOLD:
                continue
            objections.append(
                Objection(
                    entry_id=section.entry_id,
                    objection=(
                        f"Low-confidence extraction cited — fact '{fact.key}' was extracted "
                        f"from {fact.provenance.detail} at confidence {fact.confidence:.2f} "
                        f"(below {_LOW_CONFIDENCE_THRESHOLD:.2f}): re-verify against the "
                        "source document before relying on it."
                    ),
                    clause_ref=clause_by_entry.get(section.entry_id),
                )
            )
    return objections


def _parse_llm_objections(
    raw: str,
    clause_by_entry: dict[str, str],
    known_entry_ids: set[str],
) -> list[Objection]:
    """Parse strict-JSON reviewer output; sanitise anything the LLM invented."""
    text = _CODE_FENCE_RE.sub("", raw.strip()).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("examiner: LLM returned non-JSON output; discarding LLM objections")
        return []
    if not isinstance(payload, list):
        logger.warning("examiner: LLM output is not a JSON array; discarding LLM objections")
        return []

    objections: list[Objection] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        entry_id = item.get("entry_id")
        objection_text = item.get("objection")
        if not isinstance(entry_id, str) or entry_id not in known_entry_ids:
            continue  # objection against a section we never showed it — invented
        if not isinstance(objection_text, str) or not objection_text.strip():
            continue

        # The examiner must never invent clause citations: keep the returned
        # clause_ref only when it exactly equals the section's checklist
        # clause_ref; otherwise sanitise it to None.
        clause_ref = item.get("clause_ref")
        expected = clause_by_entry.get(entry_id)
        if not isinstance(clause_ref, str) or expected is None or clause_ref != expected:
            clause_ref = None

        objections.append(
            Objection(
                entry_id=entry_id,
                objection=objection_text.strip(),
                clause_ref=clause_ref,
            )
        )
    return objections


async def _llm_pass(
    sections: list[GeneratedSection],
    clause_by_entry: dict[str, str],
) -> list[Objection]:
    """SME-exchange reviewer via grounded_complete; skips silently when offline."""
    if not sections:
        return []

    blocks: list[str] = []
    for section in sections:
        clause = clause_by_entry.get(section.entry_id)
        clause_line = (
            f"clause_ref: {clause}" if clause else "clause_ref: (not provided — use null)"
        )
        blocks.append(
            f"entry_id: {section.entry_id}\n"
            f"section: {section.section}\n"
            f"{clause_line}\n"
            f"text:\n{section.text}"
        )
    user = "Draft DRHP sections under review:\n\n" + "\n\n---\n\n".join(blocks)
    context_fact_ids = sorted({c.fact_id for s in sections for c in s.citations})

    try:
        response = await llm_client.grounded_complete(
            system=_EXAMINER_SYSTEM,
            user=user,
            context_fact_ids=context_fact_ids,
            temperature=0.0,
        )
    except _LLM_SKIP_ERRORS:
        logger.info("examiner: LLM unavailable; deterministic objections only")
        return []

    known_entry_ids = {section.entry_id for section in sections}
    return _parse_llm_objections(response.text, clause_by_entry, known_entry_ids)


async def examine(
    sections: list[GeneratedSection],
    checklist: Checklist | None = None,
    *,
    contradictions: list[Contradiction] | None = None,
    boilerplate_flags: list[BoilerplateFlag] | None = None,
    arithmetic_findings: Sequence[ArithmeticFindingLike] | None = None,
    store: FactStore | None = None,
) -> list[Objection]:
    """Raise reviewer objections against generated sections.

    The deterministic pass always runs (offline-safe); the LLM pass adds
    reviewer-style objections when a provider is configured. Passing the
    ``checklist`` lets objections carry the section's clause_ref and lets the
    LLM pass validate returned clause references against it — without a
    checklist, every clause_ref is None (never invented).

    The optional keyword arguments feed other validators' outputs into the
    deterministic pass (each defaults to ``None`` — behaviour unchanged):

    - ``contradictions``: cross-section contradictions to demand reconciled.
    - ``boilerplate_flags``: flagged generic spans to demand rewritten.
    - ``arithmetic_findings``: duck-typed items with ``.kind``/``.detail``/
      ``.clause_ref`` (see ``ArithmeticFindingLike``).
    - ``store``: fact store used to flag cited low-confidence document
      extractions for re-verification.

    Objection order: existing per-section objections first, then contradiction,
    arithmetic, boilerplate, low-confidence, and finally any LLM objections.
    """
    clause_by_entry: dict[str, str] = {}
    role_by_entry: dict[str, str] = {}
    if checklist is not None:
        for entry in checklist.entries:
            clause_by_entry[entry.id] = entry.clause_ref
            role_by_entry[entry.id] = entry.responsible_role.value

    objections = _deterministic_pass(sections, clause_by_entry, role_by_entry)
    if contradictions:
        objections.extend(_contradiction_objections(contradictions, clause_by_entry))
    if arithmetic_findings:
        objections.extend(
            _arithmetic_objections(arithmetic_findings, sections, set(clause_by_entry))
        )
    if boilerplate_flags:
        objections.extend(_boilerplate_objections(boilerplate_flags, sections, clause_by_entry))
    if store is not None:
        objections.extend(_low_confidence_objections(sections, store, clause_by_entry))
    objections.extend(await _llm_pass(sections, clause_by_entry))
    return objections
