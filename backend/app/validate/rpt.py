"""Related Party Transaction (RPT) cross-check validator.

Scans confirmed facts for RPT-related disclosures and cross-checks:
1. Every entity named in RPT disclosures should also appear in the
   promoter group entity list (or be flagged as undisclosed).
2. RPT amounts flagged if no materiality assessment is provided.

Deterministic — no LLM. References ICDR Sch. VI Part A, para (6)(D)
(RPT summary, inserted by the 2026 Amendment) and para (11)(A)(g)
(related party disclosures in financial statements).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore

RPTSeverity = Literal["blocker", "material", "minor"]


class RPTFinding(BaseModel):
    kind: str
    detail: str
    entity: str | None = None
    severity: RPTSeverity


# Fact keys for RPT-related data
_RPT_ENTITY_KEYS = {"related_party_names", "rpt_entities", "related_party_transactions"}
_PROMOTER_GROUP_KEYS = {"promoter_group_entities", "promoter_group_names", "promoter_group"}


def check_rpt(store: FactStore) -> list[RPTFinding]:
    """Cross-check RPT disclosures against promoter group data.

    Only examines confirmed facts — unconfirmed proposals are not yet
    part of the draft.
    """
    findings: list[RPTFinding] = []
    confirmed = store.all_confirmed()

    # Collect RPT entity names
    rpt_entities: set[str] = set()
    has_rpt_data = False
    for fact in confirmed:
        if fact.key in _RPT_ENTITY_KEYS:
            has_rpt_data = True
            if isinstance(fact.value, list):
                rpt_entities.update(str(v).strip() for v in fact.value if v)
            elif isinstance(fact.value, str) and fact.value.strip():
                rpt_entities.add(fact.value.strip())

    # Collect promoter group entity names
    promoter_entities: set[str] = set()
    for fact in confirmed:
        if fact.key in _PROMOTER_GROUP_KEYS:
            if isinstance(fact.value, list):
                promoter_entities.update(str(v).strip() for v in fact.value if v)
            elif isinstance(fact.value, str) and fact.value.strip():
                promoter_entities.add(fact.value.strip())

    # Cross-check: RPT entities should appear in promoter group
    if rpt_entities and promoter_entities:
        for entity in sorted(rpt_entities):
            # Case-insensitive match
            if not any(entity.lower() == pg.lower() for pg in promoter_entities):
                findings.append(RPTFinding(
                    kind="rpt_entity_not_in_promoter_group",
                    detail=(
                        f"Entity \"{entity}\" appears in related party transactions "
                        f"but is not listed in the promoter group disclosure. "
                        f"ICDR Sch. VI Part A, para (10)(G) requires complete "
                        f"promoter group disclosure."
                    ),
                    entity=entity,
                    severity="material",
                ))

    # Check: RPT data exists but no materiality disclosure
    rpt_amount_keys = {"rpt_total_amount_paise", "related_party_transaction_amount_paise"}
    has_rpt_amounts = any(f.key in rpt_amount_keys for f in confirmed)

    materiality_keys = {"rpt_materiality_policy", "rpt_materiality_threshold"}
    has_materiality = any(f.key in materiality_keys for f in confirmed)

    if has_rpt_data and has_rpt_amounts and not has_materiality:
        findings.append(RPTFinding(
            kind="rpt_missing_materiality",
            detail=(
                "Related party transaction amounts are disclosed but no materiality "
                "policy or threshold is provided. Consider adding the board's policy "
                "on RPT materiality per ICDR Sch. VI Part A, para (6)(D)."
            ),
            severity="minor",
        ))

    # Check: no RPT data at all (warning, not a blocker — it's possible
    # an issuer genuinely has none, but it's unusual enough to flag)
    if not has_rpt_data:
        findings.append(RPTFinding(
            kind="rpt_no_data",
            detail=(
                "No related party transaction data found in confirmed facts. "
                "If the issuer has related party transactions, these must be "
                "disclosed per ICDR Sch. VI Part A, para (6)(D) and (11)(A)(g). "
                "If none exist, consider adding an explicit nil declaration."
            ),
            severity="minor",
        ))

    return findings
