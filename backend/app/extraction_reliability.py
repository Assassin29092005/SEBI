"""Extraction reliability tracking: turns the corrections this app already
records into a signal about how trustworthy extraction actually is.

An "extracted" fact — one sourced from a document, a lookup connector, or a
role-tagged upload, as opposed to a promoter typing an answer directly into
the wizard — carries a real risk of being wrong: OCR misreads a digit, an
LLM extraction proposal misattributes a snippet, a lookup returns a stale
value. Every such fact is a *proposal* until confirmed (see app.facts), and
can be *corrected* afterwards if it turns out to be wrong (app.facts_repo
.correct). Until now nothing aggregated those corrections into anything —
each one just sat in the fact-provenance chain and the audit log,
individually true but collectively invisible.

The banker-correction case is the one this module exists to surface: a
promoter's due-diligence-independent self-correction is useful signal, but
a BANKER correcting a fact they did NOT supply — something main.py's
``_require_can_correct_fact`` allows specifically for this reason — is a
real due-diligence catch. ``banker_caught_count`` isolates exactly that.

Deterministic, no LLM, computed fresh from the fact store on every request
(this app's fact volume per drafting cycle is small — see CLAUDE.md's
Known Limitations for the audit log's O(n) caveat, which does NOT apply
here since this only reads, never writes, on every call).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.facts import Fact, FactStore, SourceKind
from app.schema.models import Role

# Facts genuinely at risk of a real extraction error — a promoter typing an
# answer into the wizard isn't "extraction," there's no OCR/LLM step that
# could have misread anything. DOCUMENT/LOOKUP/ROLE_UPLOAD all sit behind a
# real extraction or lookup pipeline (see app.intake.uploads, app.intake
# .litigation) with a real, non-zero error rate worth tracking.
_EXTRACTED_KINDS = frozenset({SourceKind.DOCUMENT, SourceKind.LOOKUP, SourceKind.ROLE_UPLOAD})

# Reuses the exact threshold app.validate.examiner already treats as
# "low confidence, flag for review" — one definition of "low," not a new
# one invented for this report.
_LOW_CONFIDENCE_THRESHOLD = 0.7
_LOW_BAND = f"low (<{_LOW_CONFIDENCE_THRESHOLD:.1f})"
_NORMAL_BAND = f"normal (>={_LOW_CONFIDENCE_THRESHOLD:.1f})"


class ExtractionReliabilityBucket(BaseModel):
    provenance_kind: str  # "document" | "lookup" | "role_upload"
    confidence_band: str
    total_facts: int
    corrected_count: int
    # None only when total_facts == 0 (never happens for a bucket that's in
    # the list at all, but keeps the type honest rather than a fake 0.0).
    correction_rate: float | None
    banker_caught_count: int


class ExtractionReliabilityReport(BaseModel):
    buckets: list[ExtractionReliabilityBucket]
    total_extracted_facts: int
    total_corrections: int
    total_banker_caught_corrections: int


def _confidence_band(confidence: float) -> str:
    return _LOW_BAND if confidence < _LOW_CONFIDENCE_THRESHOLD else _NORMAL_BAND


def _correction_chain(fact_id: str, corrections_of: dict[str, list[Fact]]) -> list[Fact]:
    """Every correction downstream of *fact_id*, oldest first.

    Walks forward through however many times a fact was re-corrected — a
    banker correction three versions deep still counts as "this original
    fact was eventually banker-caught," not just a same-step check.
    """
    chain: list[Fact] = []
    seen = {fact_id}
    current_id = fact_id
    while True:
        next_facts = corrections_of.get(current_id)
        if not next_facts:
            return chain
        # Normal flow: correct() always targets the CURRENT fact_id, so a
        # given fact_id is superseded by at most one other fact. Sorting by
        # created_at is a defensive tie-break for a data anomaly, not the
        # expected case.
        next_fact = sorted(next_facts, key=lambda f: f.created_at)[0]
        if next_fact.fact_id in seen:  # guard against a corrupt/cyclic chain
            return chain
        chain.append(next_fact)
        seen.add(next_fact.fact_id)
        current_id = next_fact.fact_id


def compute_reliability(store: FactStore) -> ExtractionReliabilityReport:
    facts = store.all_facts()
    corrections_of: dict[str, list[Fact]] = {}
    for fact in facts:
        if fact.provenance.supersedes:
            corrections_of.setdefault(fact.provenance.supersedes, []).append(fact)

    bucket_totals: dict[tuple[str, str], int] = {}
    bucket_corrected: dict[tuple[str, str], int] = {}
    bucket_banker_caught: dict[tuple[str, str], int] = {}
    total_extracted = 0
    total_corrections = 0
    total_banker_caught = 0

    for fact in facts:
        # Only ORIGINAL facts are the denominator — a correction fact isn't
        # itself a new "extraction" to track separately, it's the outcome
        # being measured for the fact it replaced.
        if fact.provenance.kind not in _EXTRACTED_KINDS or fact.provenance.supersedes:
            continue
        total_extracted += 1
        key = (fact.provenance.kind.value, _confidence_band(fact.confidence))
        bucket_totals[key] = bucket_totals.get(key, 0) + 1

        chain = _correction_chain(fact.fact_id, corrections_of)
        if not chain:
            continue
        bucket_corrected[key] = bucket_corrected.get(key, 0) + 1
        total_corrections += 1
        banker_caught = fact.supplied_by != Role.BANKER and any(
            c.corrected_by_role == Role.BANKER for c in chain
        )
        if banker_caught:
            bucket_banker_caught[key] = bucket_banker_caught.get(key, 0) + 1
            total_banker_caught += 1

    buckets = [
        ExtractionReliabilityBucket(
            provenance_kind=kind,
            confidence_band=band,
            total_facts=total,
            corrected_count=bucket_corrected.get((kind, band), 0),
            correction_rate=(bucket_corrected.get((kind, band), 0) / total) if total else None,
            banker_caught_count=bucket_banker_caught.get((kind, band), 0),
        )
        for (kind, band), total in sorted(bucket_totals.items())
    ]
    return ExtractionReliabilityReport(
        buckets=buckets,
        total_extracted_facts=total_extracted,
        total_corrections=total_corrections,
        total_banker_caught_corrections=total_banker_caught,
    )
