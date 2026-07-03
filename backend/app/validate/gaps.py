"""Gap check: checklist schema vs. fact store, with role-aware routing.

Every gap is routed: promoter-fixable, needs-your-auditor, or
needs-your-merchant-banker. This routing is what makes "substantially
complete" an honest, defensible claim.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.facts import FactStore
from app.schema.models import Checklist, Role, Severity


class Gap(BaseModel):
    entry_id: str
    section: str
    missing_fact_key: str
    clause_ref: str
    routed_to: Role
    severity: Severity


class GapReport(BaseModel):
    gaps: list[Gap]

    def by_role(self) -> dict[Role, list[Gap]]:
        routed: dict[Role, list[Gap]] = {}
        for gap in self.gaps:
            routed.setdefault(gap.routed_to, []).append(gap)
        return routed


def check_gaps(checklist: Checklist, store: FactStore) -> GapReport:
    gaps: list[Gap] = []
    for entry in checklist.entries:
        if entry.stub:
            continue
        for fact_key in entry.required_facts:
            if not store.confirmed_by_key(fact_key):
                gaps.append(
                    Gap(
                        entry_id=entry.id,
                        section=entry.section,
                        missing_fact_key=fact_key,
                        clause_ref=entry.clause_ref,
                        routed_to=entry.responsible_role,
                        severity=entry.severity,
                    )
                )
    return GapReport(gaps=gaps)
