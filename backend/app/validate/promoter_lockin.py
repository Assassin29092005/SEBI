"""Promoter lock-in computation: minimum promoters' contribution (MPC) and the
mandatory 3-year / 2-year / 1-year lock-in split.

Purely deterministic integer arithmetic over CONFIRMED facts — no LLM, no
floats. Two rules, verified against ``data/regulation/chapter_ix_sme_ipo.txt``:

- Reg. 236(1)/(2)(a): promoters must hold at least 20% of POST-issue capital
  as the minimum promoters' contribution (MPC).
- Reg. 238(a)-(b): the MPC tranche is locked in for 3 years from allotment;
  any promoter holding IN EXCESS of the MPC is locked in separately — 50% of
  the excess for 2 years, the remaining 50% for 1 year. A single flat
  lock-in period applied to a promoter's entire post-issue holding is only
  correct when that promoter holds no more than their MPC share; once a
  promoter's holding exceeds the 20% MPC pool, the excess portion needs its
  own 2-year/1-year split, which this app's ``promoter_shareholding[]``
  fact (one ``lock_in_years`` int per promoter) cannot currently represent.

Post-issue share count is not known exactly at draft stage — only a price
BAND is fixed, the final issue price is set later. New shares issued
(``issue_size_paise / price_per_share``) is largest at the FLOOR price (more
shares needed to raise the same money), which also makes post-issue dilution
largest and the promoters' post-issue percentage smallest — the floor price
is therefore the conservative/binding scenario for both checks below, and is
what this module evaluates against.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from app.facts import FactStore

_MPC_CLAUSE = (
    "ICDR Reg. 236(1), 236(2)(a) — minimum promoters' contribution "
    "(20% of post-issue capital)"
)
_LOCKIN_CLAUSE = (
    "ICDR Reg. 238(a) (MPC: 3-year lock-in) and Reg. 238(b) "
    "(excess holding: 50% for 2 years, 50% for 1 year)"
)

_MPC_PERCENT = 20

FindingKind = Literal["mpc_shortfall", "lockin_bifurcation_missing"]
FindingSeverity = Literal["blocker", "material", "minor"]


class PromoterLockinFinding(BaseModel):
    kind: FindingKind
    detail: str
    severity: FindingSeverity
    clause_ref: str


def _int(value: Any) -> int | None:  # noqa: ANN401 — fact values are untyped by design
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _pre_issue_total_shares(store: FactStore) -> int | None:
    total = 0
    found = False
    for fact in store.confirmed_by_key("share_allotments[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            shares = _int(item.get("shares"))
            if shares is not None:
                total += shares
                found = True
    return total if found else None


def _promoter_pre_issue_shares(store: FactStore) -> int | None:
    total = 0
    found = False
    for fact in store.confirmed_by_key("promoter_shareholding[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            shares = _int(item.get("shares_held_pre_issue"))
            if shares is not None:
                total += shares
                found = True
    return total if found else None


def _flat_lockin_promoters(store: FactStore) -> list[tuple[str, int]]:
    """Promoters whose disclosure gives one flat ``lock_in_years`` for their whole stake."""
    out: list[tuple[str, int]] = []
    for fact in store.confirmed_by_key("promoter_shareholding[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            years = _int(item.get("lock_in_years"))
            name = item.get("promoter")
            if years is not None and isinstance(name, str):
                out.append((name, years))
    return out


def _new_shares_at_floor(store: FactStore, pre_issue_total: int) -> int | None:
    issue_size_values = [
        v
        for fact in store.confirmed_by_key("issue_size_paise")
        if (v := _int(fact.value)) is not None
    ]
    floor_values = [
        v
        for fact in store.confirmed_by_key("price_band")
        if isinstance(fact.value, dict)
        and (v := _int(fact.value.get("floor_price_paise"))) is not None
    ]
    if not issue_size_values or not floor_values:
        return None
    # Contradicted inputs (multiple confirmed values): the largest issue size
    # raised at the smallest floor price is the maximally-dilutive scenario —
    # evaluate that one, same "evaluate the worst case" pattern as app.validate.arithmetic.
    issue_size = max(issue_size_values)
    floor_price = min(floor_values)
    if floor_price <= 0:
        return None
    return issue_size // floor_price


def check_promoter_lockin(store: FactStore) -> list[PromoterLockinFinding]:
    """Validate minimum promoters' contribution and lock-in bifurcation.

    Returns an empty list when required inputs are not yet confirmed (the
    gap checker covers missing facts) — same convention as
    :func:`app.validate.arithmetic.check_arithmetic`.
    """
    pre_issue_total = _pre_issue_total_shares(store)
    promoter_shares = _promoter_pre_issue_shares(store)
    if pre_issue_total is None or promoter_shares is None or pre_issue_total <= 0:
        return []

    new_shares = _new_shares_at_floor(store, pre_issue_total)
    if new_shares is None:
        return []
    post_issue_total = pre_issue_total + new_shares
    if post_issue_total <= 0:
        return []

    mpc_shares = (post_issue_total * _MPC_PERCENT) // 100
    findings: list[PromoterLockinFinding] = []

    if promoter_shares < mpc_shares:
        promoter_percent = promoter_shares * 10_000 // post_issue_total
        findings.append(
            PromoterLockinFinding(
                kind="mpc_shortfall",
                detail=(
                    f"Promoters would hold {promoter_shares:,} shares "
                    f"({promoter_percent // 100}.{promoter_percent % 100:02d}%) of the "
                    f"{post_issue_total:,}-share post-issue capital (computed at the price "
                    f"band floor) — below the {_MPC_PERCENT}% minimum promoters' "
                    f"contribution ({mpc_shares:,} shares)."
                ),
                severity="blocker",
                clause_ref=_MPC_CLAUSE,
            )
        )
        return findings  # bifurcation is moot with no excess holding to speak of

    excess_shares = promoter_shares - mpc_shares
    if excess_shares <= 0:
        return findings

    flat = _flat_lockin_promoters(store)
    if flat:
        names = ", ".join(f"{name} ({years}y)" for name, years in flat)
        findings.append(
            PromoterLockinFinding(
                kind="lockin_bifurcation_missing",
                detail=(
                    f"The promoter group's post-issue holding exceeds the {_MPC_PERCENT}% "
                    f"minimum promoters' contribution by {excess_shares:,} shares. That excess "
                    "must be locked in separately from the MPC — 50% of it for 2 years and the "
                    "remaining 50% for 1 year — not folded into a single lock-in figure for the "
                    f"promoter's whole stake. Current disclosure gives one flat lock_in_years per "
                    f"promoter with no MPC/excess split: {names}."
                ),
                severity="material",
                clause_ref=_LOCKIN_CLAUSE,
            )
        )
    return findings
