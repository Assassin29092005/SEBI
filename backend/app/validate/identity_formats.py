"""Format validation for statutory identifiers: PAN, CIN, GSTIN, DIN.

Purely deterministic regex checks over CONFIRMED facts — no LLM. These are
government-issued identifier formats (Income Tax Dept. / MCA / GSTN), not
themselves ICDR clauses, but the *disclosure* of each is an ICDR requirement
(issuer particulars, promoter particulars, director particulars, subsidiary
particulars — see the clause refs on each finding below). A malformed PAN/CIN
isn't a stylistic nit: it means either a typo that will bounce at RoC/exchange
filing, or a field that was never actually validated against the source
document during extraction/confirmation.

Scope: this checks whichever of these fields are actually PRESENT in the
confirmed facts — it never demands a field exist (that's the gap checker's
job) and never guesses at a missing GSTIN. ``issuer_identity`` and the other
keys here are free-form dicts (see ``app.facts.Fact.value: Any``), so a
promoter can supply a GSTIN under ``issuer_identity["gstin"]`` today even
though the wizard doesn't have a dedicated prompt for it yet — if they do,
it's validated.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from app.facts import FactStore

FindingKind = Literal["invalid_pan", "invalid_cin", "invalid_gstin", "invalid_din"]
FindingSeverity = Literal["blocker", "material", "minor"]

# Official formats:
# - PAN (Income Tax Dept.): 5 letters + 4 digits + 1 letter, e.g. AAACS0000A.
# - CIN (MCA, Companies Act 2013 s.7): listing-status letter (U/L) + 5-digit
#   industry code + 2-letter state code + 4-digit incorporation year +
#   3-letter ownership-type code (PLC/PTC/OPC/...) + 6-digit registration
#   number, e.g. U01100MH2015PLC000000.
# - GSTIN (GSTN): 2-digit state code + 10-char PAN + 1-digit entity code +
#   literal "Z" + 1 checksum char.
# - DIN (MCA, Companies Act 2013 s.153): 8 digits.
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_CIN_RE = re.compile(r"^[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_DIN_RE = re.compile(r"^[0-9]{8}$")

_ISSUER_CLAUSE = "ICDR Sch. VI Part A, para (7)(A)–(F) — registered office and issuer particulars"
_PROMOTER_CLAUSE = "ICDR Sch. VI Part A, para (8)(A)–(B), para (10)(G) — promoter particulars"
_DIRECTOR_CLAUSE = "ICDR Sch. VI Part A, para (10)(F) — board of directors particulars"
_SUBSIDIARY_CLAUSE = "ICDR Sch. VI Part A, para (10)(D) — history and corporate structure"


class IdentityFormatFinding(BaseModel):
    kind: FindingKind
    entity: str            # human label, e.g. "issuer", "promoter: Rakesh Menon"
    field: str              # "pan" | "cin" | "gstin" | "din"
    value: str
    detail: str
    severity: FindingSeverity
    clause_ref: str


def _as_str(value: Any) -> str | None:  # noqa: ANN401 — fact values are untyped by design
    return value.strip() if isinstance(value, str) and value.strip() else None


def _check_pan(
    entity: str, raw: Any, *, clause_ref: str, severity: FindingSeverity
) -> IdentityFormatFinding | None:
    value = _as_str(raw)
    if value is None or _PAN_RE.match(value):
        return None
    return IdentityFormatFinding(
        kind="invalid_pan",
        entity=entity,
        field="pan",
        value=value,
        detail=(
            f"{entity}: PAN {value!r} does not match the Income Tax Department format "
            "(5 letters, 4 digits, 1 letter — e.g. AAACS0000A)."
        ),
        severity=severity,
        clause_ref=clause_ref,
    )


def _check_cin(
    entity: str, raw: Any, *, clause_ref: str, severity: FindingSeverity
) -> IdentityFormatFinding | None:
    value = _as_str(raw)
    if value is None or _CIN_RE.match(value):
        return None
    return IdentityFormatFinding(
        kind="invalid_cin",
        entity=entity,
        field="cin",
        value=value,
        detail=(
            f"{entity}: CIN {value!r} does not match the MCA format (listing status "
            "U/L + 5-digit industry code + 2-letter state code + 4-digit year + "
            "3-letter ownership type + 6-digit registration number — e.g. "
            "U01100MH2015PLC000000)."
        ),
        severity=severity,
        clause_ref=clause_ref,
    )


def _check_gstin(
    entity: str, raw: Any, *, clause_ref: str, severity: FindingSeverity
) -> IdentityFormatFinding | None:
    value = _as_str(raw)
    if value is None or _GSTIN_RE.match(value):
        return None
    return IdentityFormatFinding(
        kind="invalid_gstin",
        entity=entity,
        field="gstin",
        value=value,
        detail=(
            f"{entity}: GSTIN {value!r} does not match the GSTN format (2-digit "
            "state code + 10-char PAN + 1-digit entity code + 'Z' + 1 checksum "
            "character — 15 characters total)."
        ),
        severity=severity,
        clause_ref=clause_ref,
    )


def _check_din(
    entity: str, raw: Any, *, clause_ref: str, severity: FindingSeverity
) -> IdentityFormatFinding | None:
    value = _as_str(raw)
    if value is None or _DIN_RE.match(value):
        return None
    return IdentityFormatFinding(
        kind="invalid_din",
        entity=entity,
        field="din",
        value=value,
        detail=f"{entity}: DIN {value!r} is not 8 digits, as required by the MCA.",
        severity=severity,
        clause_ref=clause_ref,
    )


def check_identity_formats(store: FactStore) -> list[IdentityFormatFinding]:
    """Validate PAN/CIN/GSTIN/DIN formats across confirmed identity facts.

    Checks whichever of ``issuer_identity``, ``promoters[]``,
    ``board_of_directors[]``, and ``subsidiaries[]`` are confirmed, and
    within each, whichever of ``pan``/``cin``/``gstin``/``din`` keys are
    actually present. Malformed values on the issuer's own identity are
    blockers (every downstream disclosure references them); malformed
    promoter/director/subsidiary identifiers are material.
    """
    findings: list[IdentityFormatFinding] = []

    for fact in store.confirmed_by_key("issuer_identity"):
        if not isinstance(fact.value, dict):
            continue
        for finding in (
            _check_pan(
                "issuer", fact.value.get("pan"), clause_ref=_ISSUER_CLAUSE, severity="blocker"
            ),
            _check_cin(
                "issuer", fact.value.get("cin"), clause_ref=_ISSUER_CLAUSE, severity="blocker"
            ),
            _check_gstin(
                "issuer", fact.value.get("gstin"), clause_ref=_ISSUER_CLAUSE, severity="blocker"
            ),
        ):
            if finding is not None:
                findings.append(finding)

    for fact in store.confirmed_by_key("promoters[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            label = f"promoter: {item.get('name', '(unnamed)')}"
            pan_finding = _check_pan(
                label, item.get("pan"), clause_ref=_PROMOTER_CLAUSE, severity="material"
            )
            if pan_finding is not None:
                findings.append(pan_finding)
            din_finding = _check_din(
                label, item.get("din"), clause_ref=_PROMOTER_CLAUSE, severity="material"
            )
            if din_finding is not None:
                findings.append(din_finding)

    for fact in store.confirmed_by_key("board_of_directors[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            label = f"director: {item.get('name', '(unnamed)')}"
            din_finding = _check_din(
                label, item.get("din"), clause_ref=_DIRECTOR_CLAUSE, severity="material"
            )
            if din_finding is not None:
                findings.append(din_finding)

    for fact in store.confirmed_by_key("subsidiaries[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            label = f"subsidiary: {item.get('name', '(unnamed)')}"
            cin_finding = _check_cin(
                label, item.get("cin"), clause_ref=_SUBSIDIARY_CLAUSE, severity="minor"
            )
            if cin_finding is not None:
                findings.append(cin_finding)

    return findings
