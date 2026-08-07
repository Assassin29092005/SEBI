"""Promoter lock-in computation: ICDR Reg. 236–242 (Chapter IX).

Computes and validates:
1. Minimum promoter contribution (≥ 20% of post-issue capital) — Reg. 236(1)
2. Lock-in periods:
   - Promoter's minimum contribution: 3 years — Reg. 238
   - Excess promoter holding: 1 year — Reg. 239
   - Pre-IPO non-promoter shareholders: 1 year — Reg. 241
3. Flags violations: insufficient contribution %, missing lock-in disclosure

All arithmetic is integer paise — never floats (see CLAUDE.md money rules).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore

LockInSeverity = Literal["blocker", "material", "minor"]

_MIN_PROMOTER_CONTRIBUTION_PCT = 20  # Reg. 236(1): minimum 20%
_PROMOTER_LOCKIN_YEARS = 3           # Reg. 238: 3 years for min contribution
_EXCESS_LOCKIN_YEARS = 1             # Reg. 239: 1 year for excess
_PRE_IPO_LOCKIN_YEARS = 1            # Reg. 241: 1 year for pre-IPO holders


class LockInFinding(BaseModel):
    kind: str
    detail: str
    severity: LockInSeverity
    clause_ref: str | None = None


def _get_paise(store: FactStore, key: str) -> int | None:
    """Get a confirmed integer paise value from the store, or None."""
    facts = store.confirmed_by_key(key)
    if not facts:
        return None
    val = facts[0].value
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return None
    return None


def check_lock_in(store: FactStore) -> list[LockInFinding]:
    """Validate promoter contribution and lock-in requirements.

    Only examines confirmed facts.
    """
    findings: list[LockInFinding] = []

    issue_size = _get_paise(store, "issue_size_paise")
    promoter_contribution = _get_paise(store, "promoter_contribution_paise")
    post_issue_capital = _get_paise(store, "post_issue_capital_paise")

    # Reg. 236(1) is a percentage of *post-issue capital*; issue size is only
    # a fallback proxy when that hasn't been confirmed yet. Gate on whichever
    # base is actually available, not on issue size specifically — gating on
    # issue size meant a draft with post-issue capital and promoter
    # contribution both confirmed got no 20%-floor check at all, and a
    # blocker-severity rule silently not running is worse than it failing.
    base = post_issue_capital or issue_size

    # Check minimum promoter contribution
    if base is not None and promoter_contribution is not None:
        if base > 0:
            contribution_pct = (promoter_contribution * 100) // base
            if contribution_pct < _MIN_PROMOTER_CONTRIBUTION_PCT:
                findings.append(LockInFinding(
                    kind="insufficient_promoter_contribution",
                    detail=(
                        f"Promoter contribution is {contribution_pct}% of "
                        f"post-issue capital, below the minimum {_MIN_PROMOTER_CONTRIBUTION_PCT}% "
                        f"required by ICDR Reg. 236(1). "
                        f"The promoter must bring in at least 20% of the post-issue capital."
                    ),
                    severity="blocker",
                    clause_ref="ICDR Reg. 236(1) — minimum promoter contribution 20%",
                ))
            else:
                findings.append(LockInFinding(
                    kind="promoter_contribution_ok",
                    detail=(
                        f"Promoter contribution is {contribution_pct}% of "
                        f"post-issue capital — meets the {_MIN_PROMOTER_CONTRIBUTION_PCT}% minimum."
                    ),
                    severity="minor",
                    clause_ref="ICDR Reg. 236(1)",
                ))
    elif base is not None and promoter_contribution is None:
        findings.append(LockInFinding(
            kind="missing_promoter_contribution",
            detail=(
                "Issue size or post-issue capital is confirmed but the promoter "
                "contribution amount is not. Promoter contribution must be "
                "disclosed and must be at least 20% of post-issue capital per "
                "ICDR Reg. 236(1)."
            ),
            severity="material",
            clause_ref="ICDR Reg. 236(1)",
        ))

    # Check lock-in period disclosures
    confirmed = store.all_confirmed()
    lock_in_keys = {"promoter_lock_in_years", "promoter_lock_in_period",
                    "pre_ipo_lock_in_years", "pre_ipo_lock_in_period"}
    has_lock_in_disclosure = any(f.key in lock_in_keys for f in confirmed)

    if not has_lock_in_disclosure and base is not None:
        findings.append(LockInFinding(
            kind="missing_lock_in_disclosure",
            detail=(
                f"No lock-in period disclosure found. ICDR requires:\n"
                f"• Reg. 238: Minimum promoter contribution locked in for "
                f"{_PROMOTER_LOCKIN_YEARS} years from allotment\n"
                f"• Reg. 239: Excess promoter holding locked in for "
                f"{_EXCESS_LOCKIN_YEARS} year\n"
                f"• Reg. 241: Pre-IPO non-promoter shareholders locked in for "
                f"{_PRE_IPO_LOCKIN_YEARS} year"
            ),
            severity="material",
            clause_ref="ICDR Reg. 238–241 — lock-in periods",
        ))

    # Validate stated lock-in periods against regulatory minimums
    for fact in confirmed:
        if fact.key in {"promoter_lock_in_years", "promoter_lock_in_period"}:
            val = fact.value
            try:
                years = int(val) if isinstance(val, (int, str)) else None
            except (ValueError, TypeError):
                years = None
            if years is not None and years < _PROMOTER_LOCKIN_YEARS:
                findings.append(LockInFinding(
                    kind="insufficient_promoter_lock_in",
                    detail=(
                        f"Stated promoter lock-in of {years} year(s) is below "
                        f"the {_PROMOTER_LOCKIN_YEARS}-year minimum per Reg. 238."
                    ),
                    severity="blocker",
                    clause_ref="ICDR Reg. 238",
                ))

    return findings
