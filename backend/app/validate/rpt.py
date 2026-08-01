"""Related-party transaction cross-check: does every related party NAMED in
the RPT summary actually appear among the parties the draft discloses
elsewhere (promoters, promoter group entities, group companies, KMP,
subsidiaries)?

``rpt_summary`` is free prose (see ``data/demo_company/wizard_answers.json``
for the shape: a paragraph, not a structured list) — there is no schema to
validate field-by-field. What IS checkable without an LLM: SEBI/Ind AS 24
related-party disclosure requires the *same* related party to be identified
consistently everywhere it's mentioned. A company-suffixed entity name
("... Menon Farms Private Limited ...") appearing in the RPT summary but
nowhere in the promoter/promoter-group/group-company/subsidiary facts is
either an undisclosed related party or a name that doesn't match how it's
spelled elsewhere in the draft — either way, a reviewer needs to see it.

Deliberately narrow extraction: only entities ending in a recognisable legal
suffix (Private Limited / Ltd / LLP / Trust / & Co. / ...) are extracted from
the free text. A broader "any capitalised phrase" heuristic would flag
ordinary prose ("the Audit Committee", "the Appellate Authority") as a false
related party — precision over recall, consistent with this project's rule
of never asserting a finding it can't stand behind.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from app.facts import FactStore

_RPT_CLAUSE = (
    "ICDR Sch. VI Part A, para (11)(A)(g); summary in para (6)(D) per "
    "ICDR (Amendment) Regulations, 2026"
)

FindingKind = Literal["rpt_party_not_otherwise_disclosed"]
FindingSeverity = Literal["blocker", "material", "minor"]

# Legal-entity suffixes that mark a capitalised phrase as a probable company/
# LLP/trust name rather than ordinary prose ("the Audit Committee").
_ENTITY_RE = re.compile(
    r"\b([A-Z][A-Za-z.&\-]*(?:\s+[A-Z][A-Za-z.&\-]*)*\s+"
    r"(?:Private Limited|Pvt\.?\s?Ltd\.?|Limited|Ltd\.?|LLP|Trust|"
    r"& Co\.?|and Co\.?))\b"
)


class RptFinding(BaseModel):
    kind: FindingKind
    entity_name: str
    detail: str
    severity: FindingSeverity
    clause_ref: str


def _normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def _known_related_party_names(store: FactStore) -> set[str]:
    names: set[str] = set()
    for key in (
        "promoter_group_entities[]",
        "group_companies[]",
        "subsidiaries[]",
        "kmp[]",
        "board_of_directors[]",
    ):
        for fact in store.confirmed_by_key(key):
            if not isinstance(fact.value, list):
                continue
            for item in fact.value:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.add(_normalize(item["name"]))
    for fact in store.confirmed_by_key("issuer_identity"):
        if isinstance(fact.value, dict) and isinstance(fact.value.get("name"), str):
            names.add(_normalize(fact.value["name"]))
    return names


def _entities_mentioned_in(text: str) -> set[str]:
    return {match.group(1).strip() for match in _ENTITY_RE.finditer(text)}


def check_rpt(store: FactStore) -> list[RptFinding]:
    """Cross-check company-suffixed entity names in ``rpt_summary`` against
    every other disclosed related party.

    Returns an empty list when ``rpt_summary`` is not yet confirmed (the gap
    checker covers missing facts) — same convention as
    :func:`app.validate.arithmetic.check_arithmetic`. Deduplicates by
    normalized name so the same undisclosed party isn't reported once per
    mention.
    """
    rpt_facts = store.confirmed_by_key("rpt_summary")
    if not rpt_facts:
        return []

    known = _known_related_party_names(store)
    seen: set[str] = set()
    findings: list[RptFinding] = []
    for fact in rpt_facts:
        if not isinstance(fact.value, str):
            continue
        for entity in sorted(_entities_mentioned_in(fact.value)):
            normalized = _normalize(entity)
            if normalized in known or normalized in seen:
                continue
            seen.add(normalized)
            findings.append(
                RptFinding(
                    kind="rpt_party_not_otherwise_disclosed",
                    entity_name=entity,
                    detail=(
                        f"The related-party transaction summary names {entity!r}, which does "
                        "not appear among the disclosed promoters, promoter group entities, "
                        "group companies, subsidiaries, or KMP. Either this is an undisclosed "
                        "related party that needs its own disclosure elsewhere, or the name is "
                        "spelled differently in the two places — related-party identification "
                        "must be consistent throughout the draft."
                    ),
                    severity="material",
                    clause_ref=_RPT_CLAUSE,
                )
            )
    return findings
