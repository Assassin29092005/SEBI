"""Indian identifier format validators: PAN, CIN, and GSTIN.

Real SME paperwork carries these identifiers everywhere — company
registration, tax filings, promoter disclosures. A digit transposed in a
CIN or a malformed PAN-format string is the kind of trivially catchable
error a first-time issuer makes and a manual review catches late. These
regex-based checks catch it at intake time.

Each validator checks **format only** — a syntactically valid PAN is not
proof that it's actually registered with the Income Tax Department, and
this app does not call any government API to verify that (there is no
documented free one). What this catches: typos, truncated identifiers,
OCR misreads that break the structure (e.g. a letter where a digit
belongs), and outright fabrication that doesn't even match the format.

Format references:
- PAN: ``[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]`` — 10 characters,
  positions are category-specific; the 4th character encodes the holder
  type (C=Company, P=Person, F=Firm, etc.).
- CIN: ``[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}`` — 21
  characters; U/L = unlisted/listed, then NIC code, state code, year of
  incorporation, ownership, and a registration number.
- GSTIN: ``[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][Z][0-9A-Z]`` — 15
  characters; first 2 = state code, next 10 = PAN of the entity, then
  entity number within that PAN + 'Z' + check digit.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore

IdentifierKind = Literal["PAN", "CIN", "GSTIN"]

# PAN: 10 uppercase alphanumeric characters.
# [A-Z]{3} — first three: alphabetic series
# [ABCFGHLJPT] — fourth: holder type
#   A=AOP, B=BOI, C=Company, F=Firm, G=Government, H=HUF, J=AJP, L=Local
#   Authority, P=Person, T=Trust
# [A-Z] — fifth: first letter of surname/name
# [0-9]{4} — sequential number
# [A-Z] — check letter
_PAN_RE = re.compile(r"^[A-Z]{3}[ABCFGHLJPT][A-Z][0-9]{4}[A-Z]$")

# CIN: 21 characters.
# [UL] — listing status (U=Unlisted, L=Listed)
# [0-9]{5} — NIC industry code (5 digits)
# [A-Z]{2} — state code
# [0-9]{4} — year of incorporation
# [A-Z]{3} — ownership type (PLC/PTC/GAP/GOI/SGC/NPL/FLC/OPC/LLP etc.)
# [0-9]{6} — registration number
_CIN_RE = re.compile(r"^[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")

# GSTIN: 15 characters.
# [0-9]{2} — state code (01–37)
# [A-Z]{5}[0-9]{4}[A-Z] — PAN of the entity (10 chars)
# [0-9A-Z] — entity number for that PAN
# Z — fixed literal
# [0-9A-Z] — check digit
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


class IdentifierFinding(BaseModel):
    kind: IdentifierKind
    fact_key: str
    value: str
    valid: bool
    detail: str


def validate_pan(value: str) -> bool:
    """True iff ``value`` matches the PAN format."""
    return bool(_PAN_RE.match(value.strip().upper()))


def validate_cin(value: str) -> bool:
    """True iff ``value`` matches the CIN format."""
    return bool(_CIN_RE.match(value.strip().upper()))


def validate_gstin(value: str) -> bool:
    """True iff ``value`` matches the GSTIN format."""
    return bool(_GSTIN_RE.match(value.strip().upper()))


# Fact keys that carry identifiers — mapped to the format they should match.
# The key names match the fact-store ontology derived from the checklist
# schema (see data/regulation/checklist.yaml). Both singular and list-valued
# facts are handled: a list value validates each element separately.
_IDENTIFIER_KEYS: dict[str, tuple[IdentifierKind, re.Pattern[str]]] = {
    "company_pan": ("PAN", _PAN_RE),
    "promoter_pan": ("PAN", _PAN_RE),
    "company_cin": ("CIN", _CIN_RE),
    "gstin": ("GSTIN", _GSTIN_RE),
    "company_gstin": ("GSTIN", _GSTIN_RE),
}


def check_identifiers(store: FactStore) -> list[IdentifierFinding]:
    """Validate format of every PAN/CIN/GSTIN fact in the store.

    Only checks confirmed facts — unconfirmed proposals haven't been
    vouched for yet, so flagging their format before the promoter even
    looks at them would be noise.
    """
    findings: list[IdentifierFinding] = []
    confirmed = store.all_confirmed()

    for fact in confirmed:
        entry = _IDENTIFIER_KEYS.get(fact.key)
        if entry is None:
            continue

        kind, pattern = entry
        # A fact's value can be a single string or a list of strings
        # (e.g. multiple promoter PANs).
        values: list[str]
        if isinstance(fact.value, list):
            values = [str(v) for v in fact.value if v]
        elif isinstance(fact.value, str) and fact.value.strip():
            values = [fact.value.strip()]
        else:
            continue

        for val in values:
            normalised = val.strip().upper()
            valid = bool(pattern.match(normalised))
            if valid:
                detail = f"{kind} {normalised} has valid format."
            else:
                detail = (
                    f"{kind} \"{normalised}\" does not match the expected format. "
                    f"Please verify and correct the value."
                )
            findings.append(
                IdentifierFinding(
                    kind=kind,
                    fact_key=fact.key,
                    value=normalised,
                    valid=valid,
                    detail=detail,
                )
            )

    return findings
