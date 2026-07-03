"""Eligibility gate against ICDR Chapter IX (Reg. 228-230), verified against
data/regulation/chapter_ix_sme_ipo.txt (consolidated text amended through
2025-03-08; 2026 amendment does not alter these criteria).

A failed gate produces a readiness report (what to fix, indicative timeline),
not a dead end — the tool broadens the SME pipeline rather than only serving
the already-ready.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

ONE_CRORE_PAISE = 1_00_00_000_00          # ₹1 crore in paise
TEN_CRORE_PAISE = 10 * ONE_CRORE_PAISE
TWENTY_FIVE_CRORE_PAISE = 25 * ONE_CRORE_PAISE


class GateResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class EligibilityInput(BaseModel):
    """Answers to the pre-wizard eligibility questions. Money in paise."""

    # Reg. 229(1)-(2): post-issue paid-up capital ceiling
    post_issue_paid_up_capital_paise: int
    # Reg. 229(6): EBITDA >= ₹1 cr in at least 2 of the last 3 FYs
    operating_profit_years: int
    min_operating_profit_paise: int          # lowest EBITDA among the qualifying years
    # Reg. 228: entities not eligible
    is_debarred_by_sebi: bool                # issuer/promoters/directors/selling shareholders
    promoter_director_of_debarred_company: bool
    is_wilful_defaulter_or_fraudulent_borrower: bool
    is_fugitive_economic_offender: bool
    has_outstanding_convertibles: bool       # other than exempt ESOPs / must-convert securities
    # Reg. 229(5): complete promoter change / >50% new promoters
    promoter_change_within_1yr: bool
    # Reg. 230(1)(f)-(g): OFS caps
    ofs_pct_of_issue: float                  # OFS portion as % of total issue size
    # Reg. 230(1)(b)-(d): demat + fully paid-up
    promoter_shares_demat: bool
    partly_paid_shares_outstanding: bool


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
    """Fail fast against Chapter IX eligibility norms (Reg. 228-230)."""
    items: list[ReadinessItem] = []

    # ── Reg. 228: hard disqualifications ──
    if data.is_debarred_by_sebi:
        items.append(
            ReadinessItem(
                criterion="Issuer/promoters/directors/selling shareholders not debarred by SEBI",
                clause_ref="ICDR Reg. 228(a)",
                current_state="A party to the issue is debarred from accessing the capital market",
                fix="Debarment must lapse or be set aside before any public issue",
                indicative_timeline="per debarment order",
            )
        )
    if data.promoter_director_of_debarred_company:
        items.append(
            ReadinessItem(
                criterion="No promoter/director is a promoter/director of a debarred company",
                clause_ref="ICDR Reg. 228(b)",
                current_state="A promoter or director is associated with a debarred company",
                fix="Resolve the association or the underlying debarment",
                indicative_timeline="per debarment order",
            )
        )
    if data.is_wilful_defaulter_or_fraudulent_borrower:
        items.append(
            ReadinessItem(
                criterion="Issuer/promoters/directors not wilful defaulters or fraudulent borrowers",
                clause_ref="ICDR Reg. 228(c)",
                current_state="Classified as wilful defaulter / fraudulent borrower",
                fix="Regularise the account and obtain declassification from the lender",
                indicative_timeline="6–18 months, lender-dependent",
            )
        )
    if data.is_fugitive_economic_offender:
        items.append(
            ReadinessItem(
                criterion="No promoter/director is a fugitive economic offender",
                clause_ref="ICDR Reg. 228(d)",
                current_state="A promoter or director is a declared fugitive economic offender",
                fix="Not curable while the declaration stands",
                indicative_timeline="per legal proceedings",
            )
        )
    if data.has_outstanding_convertibles:
        items.append(
            ReadinessItem(
                criterion="No outstanding convertible securities or rights to equity",
                clause_ref="ICDR Reg. 228(e) (exempt: compliant ESOPs; fully paid convertibles converting before RHP/prospectus)",
                current_state="Outstanding convertibles or option rights exist",
                fix="Convert or extinguish them before filing (or fit within the exemptions)",
                indicative_timeline="1–3 months",
            )
        )

    # ── Reg. 229: eligibility requirements ──
    if data.post_issue_paid_up_capital_paise > TWENTY_FIVE_CRORE_PAISE:
        items.append(
            ReadinessItem(
                criterion="Post-issue paid-up capital ≤ ₹25 crore",
                clause_ref="ICDR Reg. 229(1)-(2)",
                current_state=f"Post-issue paid-up capital ₹{data.post_issue_paid_up_capital_paise / ONE_CRORE_PAISE:.1f} cr exceeds ₹25 cr",
                fix="Above ₹25 cr post-issue capital, a main-board IPO applies instead — outside this tool's scope",
                indicative_timeline="n/a (different listing route)",
            )
        )
    if data.operating_profit_years < 2 or data.min_operating_profit_paise < ONE_CRORE_PAISE:
        items.append(
            ReadinessItem(
                criterion="Operating profit (EBITDA) ≥ ₹1 crore in at least 2 of the last 3 FYs",
                clause_ref="ICDR Reg. 229(6)",
                current_state=(
                    f"EBITDA ≥ ₹1 cr in {data.operating_profit_years} of the last 3 FYs"
                    if data.operating_profit_years < 2
                    else "Qualifying years' EBITDA below ₹1 crore"
                ),
                fix="Build the operating track record; ensure statements are peer-review audited (Reg. 229 proviso)",
                indicative_timeline="1–2 financial years",
            )
        )
    if data.promoter_change_within_1yr:
        items.append(
            ReadinessItem(
                criterion="No complete promoter change (or >50% new promoters) in the last year",
                clause_ref="ICDR Reg. 229(5)",
                current_state="Promoter change within the last year",
                fix="File the draft offer document only after one year from the final change",
                indicative_timeline="up to 12 months",
            )
        )

    # ── Reg. 230: general conditions ──
    if data.ofs_pct_of_issue > 20.0:
        items.append(
            ReadinessItem(
                criterion="Offer for sale ≤ 20% of total issue size",
                clause_ref="ICDR Reg. 230(1)(f)",
                current_state=f"OFS is {data.ofs_pct_of_issue:.1f}% of the issue",
                fix="Reduce the OFS component (each seller also capped at 50% of pre-issue holding, Reg. 230(1)(g))",
                indicative_timeline="restructure the issue",
            )
        )
    if not data.promoter_shares_demat:
        items.append(
            ReadinessItem(
                criterion="All promoter-held specified securities in demat form",
                clause_ref="ICDR Reg. 230(1)(d)",
                current_state="Promoter holdings not fully dematerialised",
                fix="Dematerialise all promoter holdings via a depository",
                indicative_timeline="2–6 weeks",
            )
        )
    if data.partly_paid_shares_outstanding:
        items.append(
            ReadinessItem(
                criterion="No partly paid-up equity shares outstanding",
                clause_ref="ICDR Reg. 230(1)(c)",
                current_state="Partly paid-up shares exist",
                fix="Make them fully paid-up or forfeit them",
                indicative_timeline="1–2 months",
            )
        )

    result = GateResult.PASS if not items else GateResult.FAIL
    return EligibilityReport(result=result, items=items)
