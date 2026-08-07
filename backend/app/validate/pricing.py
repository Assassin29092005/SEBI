"""Pricing / valuation cross-check validator.

Validates pricing and valuation consistency:
1. Issue price ≥ face value (basic sanity)
2. PE ratio computation from EPS and issue price
3. Computed PE vs. stated PE cross-check
4. Industry PE comparison flag (if sector PE data exists)

All monetary values are integer paise — never floats (see CLAUDE.md).
PE ratios are computed as Decimal for precision, then compared.

References: ICDR Sch. VI Part A, para (9)(K) — basis for issue price
(adjusted EPS, P/E, RoNW, NAV).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore

PricingSeverity = Literal["blocker", "material", "minor"]


class PricingFinding(BaseModel):
    kind: str
    detail: str
    severity: PricingSeverity
    clause_ref: str | None = None


def _get_paise(store: FactStore, key: str) -> int | None:
    """Get a confirmed integer paise value from the store."""
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


def _get_decimal(store: FactStore, key: str) -> Decimal | None:
    """Get a confirmed decimal value from the store."""
    facts = store.confirmed_by_key(key)
    if not facts:
        return None
    val = facts[0].value
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return None


def check_pricing(store: FactStore) -> list[PricingFinding]:
    """Validate pricing and valuation consistency.

    Only examines confirmed facts.
    """
    findings: list[PricingFinding] = []

    face_value_paise = _get_paise(store, "face_value_paise")
    issue_price_paise = _get_paise(store, "issue_price_paise")
    # Also check band endpoints
    price_band_low_paise = _get_paise(store, "price_band_low_paise")
    price_band_high_paise = _get_paise(store, "price_band_high_paise")

    # Use the best available price
    effective_price = issue_price_paise or price_band_high_paise

    # 1. Issue price ≥ face value
    if face_value_paise is not None and effective_price is not None:
        if effective_price < face_value_paise:
            findings.append(PricingFinding(
                kind="price_below_face_value",
                detail=(
                    f"Issue price ({effective_price} paise) is below face value "
                    f"({face_value_paise} paise). Shares cannot be issued below "
                    f"face value under the Companies Act, 2013 (Section 53)."
                ),
                severity="blocker",
                clause_ref="Companies Act 2013 s.53; ICDR Sch. VI Part A, para (9)(K)",
            ))
        elif effective_price == face_value_paise:
            findings.append(PricingFinding(
                kind="price_at_face_value",
                detail="Issue price equals face value — at-par issue.",
                severity="minor",
                clause_ref="ICDR Sch. VI Part A, para (9)(K)",
            ))

    # 2. Price band spread check (SME IPOs: band should not exceed 20%)
    if price_band_low_paise is not None and price_band_high_paise is not None:
        if price_band_low_paise > 0:
            spread = price_band_high_paise - price_band_low_paise
            spread_pct = (spread * 100) // price_band_low_paise
            if spread_pct > 20:
                findings.append(PricingFinding(
                    kind="excessive_price_band_spread",
                    detail=(
                        f"Price band spread is {spread_pct}% (low: {price_band_low_paise} "
                        f"paise, high: {price_band_high_paise} paise). SEBI guidelines "
                        f"recommend the spread not exceed 20%."
                    ),
                    severity="material",
                    clause_ref="ICDR Sch. VI Part A, para (9)(K)",
                ))

    # 3. PE ratio computation and cross-check
    eps = _get_decimal(store, "basic_eps")
    if eps is None:
        eps = _get_decimal(store, "diluted_eps")

    stated_pe = _get_decimal(store, "pe_ratio")

    if effective_price is not None and eps is not None and eps > 0:
        # Compute PE: price in rupees / EPS in rupees
        price_rupees = Decimal(effective_price) / Decimal(100)  # paise → rupees
        computed_pe = price_rupees / eps

        if stated_pe is not None:
            # Cross-check computed vs. stated
            diff = abs(computed_pe - stated_pe)
            if diff > Decimal("0.5"):
                findings.append(PricingFinding(
                    kind="pe_ratio_mismatch",
                    detail=(
                        f"Computed PE ratio ({computed_pe:.2f}x from price "
                        f"₹{price_rupees:.2f} / EPS ₹{eps:.2f}) differs from "
                        f"stated PE ratio ({stated_pe:.2f}x) by {diff:.2f}. "
                        f"Please verify the basis for issue price calculation."
                    ),
                    severity="material",
                    clause_ref="ICDR Sch. VI Part A, para (9)(K) — basis for issue price",
                ))
            else:
                findings.append(PricingFinding(
                    kind="pe_ratio_consistent",
                    detail=f"PE ratio ({computed_pe:.2f}x) is consistent with stated value.",
                    severity="minor",
                    clause_ref="ICDR Sch. VI Part A, para (9)(K)",
                ))

        # 4. Industry PE comparison
        industry_pe = _get_decimal(store, "industry_pe_ratio")
        if industry_pe is not None and industry_pe > 0:
            premium_pct = ((computed_pe - industry_pe) / industry_pe * 100)
            if premium_pct > Decimal("50"):
                findings.append(PricingFinding(
                    kind="significant_pe_premium",
                    detail=(
                        f"Issue PE ({computed_pe:.2f}x) is {premium_pct:.0f}% above "
                        f"industry PE ({industry_pe:.2f}x). This significant premium "
                        f"must be justified in the basis for issue price section."
                    ),
                    severity="material",
                    clause_ref="ICDR Sch. VI Part A, para (9)(K)",
                ))
    elif effective_price is not None and eps is None:
        findings.append(PricingFinding(
            kind="missing_eps",
            detail=(
                "Issue price is set but EPS (basic or diluted) is not confirmed. "
                "EPS is required to compute the PE ratio for the basis-for-issue-price "
                "disclosure per ICDR Sch. VI Part A, para (9)(K)."
            ),
            severity="material",
            clause_ref="ICDR Sch. VI Part A, para (9)(K)",
        ))

    return findings
