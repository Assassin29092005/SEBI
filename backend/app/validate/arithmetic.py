"""Objects-of-the-Issue arithmetic check: do the use-of-proceeds numbers tie out?

Purely deterministic integer arithmetic over CONFIRMED facts only — no LLM,
no floats (see CLAUDE.md money rules). Three rules:

- the objects (plus GCP) must not exceed the issue size
  (ICDR Sch. VI Part A, para (9) — Objects of the Issue);
- proceeds left unallocated beyond a 5% tolerance must be disclosed
  (issue expenses / residual allocation);
- General Corporate Purposes is capped at the lower of 15% of the issue
  size or ₹10 crore (ICDR Reg. 230(2), as applied to Chapter IX issues).

The planted demo contradiction can leave TWO live confirmed values for
``issue_size_paise``. Rather than crash or pick one arbitrarily, the check
evaluates the arithmetic against each value and says so in the finding —
"the numbers only tie out against one of your two issue sizes" is exactly
the story the validation suite exists to tell.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from app.facts import FactStore

_OBJECTS_CLAUSE = "ICDR Sch. VI Part A, para (9) — Objects of the Issue"
# Reg. 230(2) verified against data/regulation/chapter_ix_sme_ipo.txt: "The
# amount for general corporate purposes ... shall not exceed fifteen per cent.
# of the amount being raised by the issuer or ₹10 crores, whichever is less."
_GCP_CLAUSE = "ICDR Reg. 230(2) — GCP cap (lower of 15% or Rs 10 crore)"

_GCP_ABSOLUTE_CAP_PAISE = 10_000_000_000  # ₹10 crore
_GCP_CAP_PERCENT = 15
_UNALLOCATED_TOLERANCE_PERCENT = 5

FindingKind = Literal[
    "objects_overallocated", "unallocated_proceeds", "gcp_cap_breach", "missing_inputs"
]
FindingSeverity = Literal["blocker", "material", "minor"]


class ArithmeticFinding(BaseModel):
    kind: FindingKind
    detail: str                       # human sentence, exact figures in lakh/crore
    expected_paise: int | None = None
    actual_paise: int | None = None
    severity: FindingSeverity
    clause_ref: str | None = None


# --------------------------------------------------------------------------
# Display helpers (display layer ONLY — money stays integer paise everywhere).
# Replicates app.generate.sections.format_inr_paise locally so the validator
# depends on nothing but the fact store.
# --------------------------------------------------------------------------

_PAISE_PER_CRORE = 10**9  # 1 crore rupees = 10^7 rupees = 10^9 paise
_PAISE_PER_LAKH = 10**7   # 1 lakh rupees  = 10^5 rupees = 10^7 paise


def _two_decimals(magnitude: int, unit: int) -> str:
    """Integer-only half-up rounding of ``magnitude/unit`` to two decimals."""
    hundredths = (magnitude * 100 + unit // 2) // unit
    whole, frac = divmod(hundredths, 100)
    return f"{whole:,}.{frac:02d}"


def _fmt(paise: int) -> str:
    """Integer paise → display string, e.g. 12_500_000_000 → "Rs 12.50 crore"."""
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    if magnitude >= _PAISE_PER_CRORE:
        return f"{sign}Rs {_two_decimals(magnitude, _PAISE_PER_CRORE)} crore"
    if magnitude >= _PAISE_PER_LAKH:
        return f"{sign}Rs {_two_decimals(magnitude, _PAISE_PER_LAKH)} lakh"
    rupees, remainder = divmod(magnitude, 100)
    if remainder:
        return f"{sign}Rs {rupees:,}.{remainder:02d}"
    return f"{sign}Rs {rupees:,}"


def _pct(part: int, whole: int) -> str:
    """``part`` as a percentage of ``whole`` to two decimals, integer math only."""
    hundredths = (part * 10_000 + whole // 2) // whole
    whole_part, frac = divmod(hundredths, 100)
    return f"{whole_part}.{frac:02d}%"


# --------------------------------------------------------------------------
# Fact extraction
# --------------------------------------------------------------------------


def _int_paise(value: Any) -> int | None:  # noqa: ANN401 — fact values are untyped by design
    """Accept only genuine ints (bool is an int subclass — reject it)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _allocated_paise(objects_values: list[Any], gcp_paise: int) -> int:
    """Sum of amount_paise over every item of every confirmed objects list, plus GCP.

    Malformed items (not a dict / no integer ``amount_paise``) contribute 0:
    schema-shape problems are the gap checker's job, not arithmetic's.
    """
    total = 0
    for value in objects_values:
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            amount = _int_paise(item.get("amount_paise"))
            if amount is not None:
                total += amount
    return total + gcp_paise


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


def check_arithmetic(store: FactStore) -> list[ArithmeticFinding]:
    """Validate Objects-of-the-Issue arithmetic against confirmed facts only.

    Unconfirmed facts never feed validation, exactly as they never feed
    generation. Returns an empty list when the numbers tie out.
    """
    issue_size_values = sorted(
        {
            paise
            for fact in store.confirmed_by_key("issue_size_paise")
            if (paise := _int_paise(fact.value)) is not None
        }
    )
    objects_facts = store.confirmed_by_key("objects_of_issue[]")

    missing = [
        key
        for key, present in (
            ("issue_size_paise", bool(issue_size_values)),
            ("objects_of_issue[]", bool(objects_facts)),
        )
        if not present
    ]
    if missing:
        return [
            ArithmeticFinding(
                kind="missing_inputs",
                detail=(
                    "Objects-of-the-issue arithmetic could not be checked: no confirmed "
                    f"value for {' or '.join(missing)}."
                ),
                severity="minor",
            )
        ]

    gcp_values = [
        paise
        for fact in store.confirmed_by_key("gcp_amount_paise")
        if (paise := _int_paise(fact.value)) is not None
    ]
    # If GCP itself is contradicted, evaluate the worst case (largest claim).
    gcp_paise = max(gcp_values) if gcp_values else 0
    allocated = _allocated_paise([fact.value for fact in objects_facts], gcp_paise)

    contradicted = len(issue_size_values) > 1
    findings: list[ArithmeticFinding] = []
    for issue_size in issue_size_values:
        note = ""
        if contradicted:
            note = (
                f" Note: the issue size itself is contradicted — {len(issue_size_values)} "
                "confirmed values exist "
                f"({', '.join(_fmt(v) for v in issue_size_values)}); this finding is "
                f"evaluated against {_fmt(issue_size)}."
            )

        if allocated > issue_size:
            findings.append(
                ArithmeticFinding(
                    kind="objects_overallocated",
                    detail=(
                        f"Objects of the issue (including GCP) total {_fmt(allocated)}, "
                        f"which exceeds the issue size of {_fmt(issue_size)} by "
                        f"{_fmt(allocated - issue_size)}.{note}"
                    ),
                    expected_paise=issue_size,
                    actual_paise=allocated,
                    severity="blocker",
                    clause_ref=_OBJECTS_CLAUSE,
                )
            )

        residual = issue_size - allocated
        if residual * 100 > issue_size * _UNALLOCATED_TOLERANCE_PERCENT:
            findings.append(
                ArithmeticFinding(
                    kind="unallocated_proceeds",
                    detail=(
                        f"{_fmt(residual)} of the {_fmt(issue_size)} issue "
                        f"({_pct(residual, issue_size)}) is not allocated to any object; "
                        "issue expenses or the intended allocation of the residual "
                        f"proceeds must be disclosed.{note}"
                    ),
                    expected_paise=issue_size,
                    actual_paise=allocated,
                    severity="material",
                    clause_ref=_OBJECTS_CLAUSE,
                )
            )

        gcp_cap = min(issue_size * _GCP_CAP_PERCENT // 100, _GCP_ABSOLUTE_CAP_PAISE)
        if gcp_paise > gcp_cap:
            findings.append(
                ArithmeticFinding(
                    kind="gcp_cap_breach",
                    detail=(
                        f"General corporate purposes of {_fmt(gcp_paise)} exceeds the "
                        f"permitted cap of {_fmt(gcp_cap)} (lower of "
                        f"{_GCP_CAP_PERCENT}% of the {_fmt(issue_size)} issue and "
                        f"{_fmt(_GCP_ABSOLUTE_CAP_PAISE)}).{note}"
                    ),
                    expected_paise=gcp_cap,
                    actual_paise=gcp_paise,
                    severity="blocker",
                    clause_ref=_GCP_CLAUSE,
                )
            )
    return findings
