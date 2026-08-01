"""Pricing/valuation cross-check: does the disclosed price band actually
satisfy SEBI's own structural constraints, and is face value consistent
across the share-capital history?

Purely deterministic integer arithmetic over CONFIRMED facts — no LLM, no
floats. Rules verified against ``data/regulation/chapter_ix_sme_ipo.txt``,
Reg. 250 ("Price and price band"):

- Reg. 250(2): the cap price shall not exceed 120% of the floor price.
- Reg. 250(3): the floor price (or the final price) shall not be less than
  the face value of the specified securities.
- Structural: a price *band* requires cap >= floor by construction; a
  band where the cap is below the floor isn't a band, it's a data error.

Also checks face-value consistency across ``share_allotments[]`` and against
``price_band.face_value_paise`` — face value doesn't change over a company's
history absent a disclosed split/consolidation (no such fact exists in this
schema), so if it doesn't match everywhere, either an allotment entry has a
typo or an undisclosed corporate action happened. This isn't a numbered ICDR
clause of its own; it rides on the same para (8) share-capital-history
disclosure the mismatched facts already belong to.

Peer P/E / RoNW / NAV sanity (``peer_comparison[]``) is deliberately NOT
cross-checked here: those are independent, externally-sourced figures for
listed peers, and the schema does not carry the issuer's OWN EPS/RoNW/NAV as
a structured fact (only inside ``restated_financials_upload``, an
auditor-supplied document this tool never parses) — there is nothing to
recompute a "does the story make sense" comparison against without inventing
a number the fact store doesn't actually hold.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from app.facts import FactStore

_BAND_SPREAD_CLAUSE = "ICDR Reg. 250(2) — cap price shall not exceed 120% of the floor price"
_FACE_VALUE_CLAUSE = "ICDR Reg. 250(3) — floor/final price shall not be less than face value"
_SHARE_CAPITAL_CLAUSE = "ICDR Sch. VI Part A, para (8)(A)–(B) — share capital history"

_CAP_TO_FLOOR_MAX_PERCENT = 120

FindingKind = Literal[
    "price_band_invalid_range",
    "price_band_spread_exceeds_cap",
    "floor_below_face_value",
    "face_value_mismatch",
]
FindingSeverity = Literal["blocker", "material", "minor"]


class PricingFinding(BaseModel):
    kind: FindingKind
    detail: str
    severity: FindingSeverity
    clause_ref: str


def _int(value: Any) -> int | None:  # noqa: ANN401 — fact values are untyped by design
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _price_bands(store: FactStore) -> list[dict[str, int]]:
    bands: list[dict[str, int]] = []
    for fact in store.confirmed_by_key("price_band"):
        if not isinstance(fact.value, dict):
            continue
        floor = _int(fact.value.get("floor_price_paise"))
        cap = _int(fact.value.get("cap_price_paise"))
        face = _int(fact.value.get("face_value_paise"))
        if floor is not None and cap is not None and face is not None:
            bands.append({"floor": floor, "cap": cap, "face": face})
    return bands


def check_pricing(store: FactStore) -> list[PricingFinding]:
    """Validate price-band structural constraints and face-value consistency.

    Returns an empty list when required inputs are not yet confirmed (the
    gap checker covers missing facts) — same convention as
    :func:`app.validate.arithmetic.check_arithmetic`.
    """
    findings: list[PricingFinding] = []

    for band in _price_bands(store):
        floor, cap, face = band["floor"], band["cap"], band["face"]

        if cap < floor:
            findings.append(
                PricingFinding(
                    kind="price_band_invalid_range",
                    detail=(
                        f"The price band's cap ({cap} paise) is below its floor ({floor} "
                        "paise) — a price band requires cap >= floor by definition."
                    ),
                    severity="blocker",
                    clause_ref=_BAND_SPREAD_CLAUSE,
                )
            )
        elif floor > 0 and cap * 100 > floor * _CAP_TO_FLOOR_MAX_PERCENT:
            spread_percent = cap * 10_000 // floor
            findings.append(
                PricingFinding(
                    kind="price_band_spread_exceeds_cap",
                    detail=(
                        f"The price band's cap ({cap} paise) is "
                        f"{spread_percent // 100}.{spread_percent % 100:02d}% of the floor "
                        f"({floor} paise) — above the {_CAP_TO_FLOOR_MAX_PERCENT}% ceiling."
                    ),
                    severity="blocker",
                    clause_ref=_BAND_SPREAD_CLAUSE,
                )
            )

        if floor < face:
            findings.append(
                PricingFinding(
                    kind="floor_below_face_value",
                    detail=(
                        f"The price band's floor ({floor} paise) is below the face value "
                        f"({face} paise) — securities cannot be priced below par."
                    ),
                    severity="blocker",
                    clause_ref=_FACE_VALUE_CLAUSE,
                )
            )

    face_values: dict[int, list[str]] = {}
    for fact in store.confirmed_by_key("share_allotments[]"):
        if not isinstance(fact.value, list):
            continue
        for item in fact.value:
            if not isinstance(item, dict):
                continue
            face = _int(item.get("face_value_paise"))
            if face is not None:
                label = f"allotment on {item.get('date', '(undated)')}"
                face_values.setdefault(face, []).append(label)
    for band in _price_bands(store):
        face_values.setdefault(band["face"], []).append("price band")

    if len(face_values) > 1:
        described = "; ".join(
            f"{face} paise ({', '.join(labels)})" for face, labels in sorted(face_values.items())
        )
        findings.append(
            PricingFinding(
                kind="face_value_mismatch",
                detail=(
                    "Face value is not consistent across the disclosed share-capital "
                    f"history and price band: {described}. Face value does not change "
                    "without a disclosed split/consolidation."
                ),
                severity="material",
                clause_ref=_SHARE_CAPITAL_CLAUSE,
            )
        )

    return findings
