"""Eligibility gate against ICDR Chapter IX / SME-exchange norms.

A failed gate produces a readiness report (what to fix, indicative timeline),
not a dead end — the tool broadens the SME pipeline rather than only serving
the already-ready.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class GateResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class EligibilityInput(BaseModel):
    """Answers to the pre-wizard eligibility questions. Money in paise."""

    post_issue_paid_up_capital_paise: int
    net_tangible_assets_paise: int
    operating_profit_years: int          # of the last 3 financial years
    is_bifr_or_insolvency: bool
    promoters_unchanged_1yr: bool


class ReadinessItem(BaseModel):
    criterion: str
    clause_ref: str
    current_state: str
    fix: str
    indicative_timeline: str


class EligibilityReport(BaseModel):
    result: GateResult
    items: list[ReadinessItem]           # empty on a clean pass


def evaluate(data: EligibilityInput) -> EligibilityReport:
    """Fail fast against Chapter IX eligibility norms.

    TODO: encode the full criteria set from data/regulation/ with verified
    clause refs and current thresholds before demo. The checks below are a
    representative starting subset.
    """
    items: list[ReadinessItem] = []

    if data.is_bifr_or_insolvency:
        items.append(
            ReadinessItem(
                criterion="No pending insolvency/winding-up proceedings",
                clause_ref="ICDR Ch. IX, Reg. 228",  # TODO verify
                current_state="Insolvency or BIFR proceedings reported",
                fix="Resolve or exit proceedings before filing",
                indicative_timeline="depends on proceedings",
            )
        )
    if data.operating_profit_years < 2:
        items.append(
            ReadinessItem(
                criterion="Operating profit in at least 2 of the last 3 FYs",
                clause_ref="ICDR Ch. IX (2024 amendment)",  # TODO verify
                current_state=f"Operating profit in {data.operating_profit_years} of last 3 FYs",
                fix="Build a profitable operating track record",
                indicative_timeline="1–2 financial years",
            )
        )

    result = GateResult.PASS if not items else GateResult.FAIL
    return EligibilityReport(result=result, items=items)
