"""Contradiction check: numeric/entity consistency across generated sections.

"Page 40 disagrees with page 12." Extract all numeric and entity claims from
the generated sections, then cross-check. The planted-error live demo depends
on this module.

Design (offline-first):
- Claim extraction is deterministic and works with no API key: cited facts are
  resolved through the fact store, and Indian-format monetary expressions
  (₹x crore / lakh, Rs. with comma groups) are regex-scanned outside cited
  spans. An optional LLM refinement pass may re-attribute uncited monetary
  mentions to known cited subjects; when the LLM is unavailable it is skipped
  silently and the deterministic claims stand unchanged.
- The LLM never invents a claim, value, or subject: refinement can only
  relabel an uncited claim with a subject that already exists among the cited
  claims of the same section.
- Monetary normalisation is exact Decimal arithmetic to integer paise
  (crore = 10^9 paise, lakh = 10^7 paise) — never floats.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from app.facts import FactStore
from app.generate.sections import GeneratedSection
from app.llm.client import grounded_complete

try:  # pragma: no cover - exercised only once the client ships LLMUnavailable
    from app.llm.client import LLMUnavailable
except ImportError:  # pragma: no cover

    class LLMUnavailable(RuntimeError):  # type: ignore[no-redef]
        """Shim until app.llm.client exports LLMUnavailable (no API key configured)."""


class Claim(BaseModel):
    section_entry_id: str
    kind: str                    # "number" | "entity" | "date"
    subject: str                 # what the claim is about, e.g. "issue_size"
    value: str
    text_span: tuple[int, int]


class Contradiction(BaseModel):
    subject: str
    claims: list[Claim]          # the mutually inconsistent claims


# --------------------------------------------------------------------------
# Indian-format monetary expressions
# --------------------------------------------------------------------------

# Amounts: Indian/Western comma grouping ("14,00,00,000", "1,234,567") or plain
# digits, with an optional decimal part.
_AMOUNT = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?"

# Either a currency marker (₹ / Rs. / Rs / INR) followed by an amount with an
# optional crore/lakh unit, or a bare amount with an explicit crore/lakh unit.
_MONEY_RE = re.compile(
    rf"(?:(?P<cur1>₹|\bRs\.?|\bINR\b)\s*(?P<amount1>{_AMOUNT})(?:\s*(?P<unit1>crores?|lakhs?)\b)?"
    rf"|(?P<amount2>{_AMOUNT})\s*(?P<unit2>crores?|lakhs?)\b)",
    re.IGNORECASE,
)

# 1 crore rupees = 10^7 rupees = 10^9 paise; 1 lakh rupees = 10^5 rupees = 10^7 paise.
_UNIT_TO_PAISE: dict[str, Decimal] = {
    "crore": Decimal(10) ** 9,
    "lakh": Decimal(10) ** 7,
}
_RUPEES_TO_PAISE = Decimal(100)


def _parse_money(text: str) -> tuple[Decimal, str | None] | None:
    """Return (amount, unit) when *text* is a whole monetary expression, else None."""
    match = _MONEY_RE.fullmatch(text.strip())
    if match is None:
        return None
    amount_raw = match.group("amount1") or match.group("amount2")
    unit_raw = match.group("unit1") or match.group("unit2")
    amount = Decimal(amount_raw.replace(",", ""))
    unit = unit_raw.lower().rstrip("s") if unit_raw else None
    return amount, unit


def _decimal_str(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def normalize(value: str) -> str:
    """Canonicalise a claim value for comparison.

    - Monetary expressions (currency marker and/or crore/lakh unit) become
      integer paise: "₹12.5 crore" -> "12500000000"; "Rs. 14,00,00,000" ->
      "14000000000". Exact Decimal arithmetic, never floats.
    - Plain numerics have grouping stripped: "1,234" -> "1234".
    - Anything else is whitespace-collapsed and casefolded.
    """
    text = value.strip()
    money = _parse_money(text)
    if money is not None:
        amount, unit = money
        paise = amount * (_UNIT_TO_PAISE[unit] if unit else _RUPEES_TO_PAISE)
        return _decimal_str(paise)
    try:
        return _decimal_str(Decimal(text.replace(",", "")))
    except InvalidOperation:
        return " ".join(text.split()).casefold()


def _parses_numeric(value: str) -> bool:
    text = value.strip()
    if _parse_money(text) is not None:
        return True
    try:
        Decimal(text.replace(",", ""))
    except InvalidOperation:
        return False
    return True


# --------------------------------------------------------------------------
# Claim extraction
# --------------------------------------------------------------------------


def _cited_claims(section: GeneratedSection, store: FactStore) -> list[Claim]:
    claims: list[Claim] = []
    for citation in section.citations:
        try:
            fact = store.get(citation.fact_id)
        except KeyError:
            continue  # dangling citation is the gap checker's problem, not ours
        value = fact.value
        is_number = (isinstance(value, int) and not isinstance(value, bool)) or _parses_numeric(
            str(value)
        )
        claims.append(
            Claim(
                section_entry_id=section.entry_id,
                kind="number" if is_number else "entity",
                subject=fact.key,
                value=str(value),
                text_span=citation.text_span,
            )
        )
    return claims


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < s_end and end > s_start for s_start, s_end in spans)


def _uncited_money_claims(section: GeneratedSection) -> list[Claim]:
    cited_spans = [c.text_span for c in section.citations]
    claims: list[Claim] = []
    for match in _MONEY_RE.finditer(section.text):
        if _overlaps_any(match.start(), match.end(), cited_spans):
            continue
        claims.append(
            Claim(
                section_entry_id=section.entry_id,
                kind="number",
                subject=f"uncited:{section.entry_id}",
                value=match.group(0),
                text_span=(match.start(), match.end()),
            )
        )
    return claims


_REFINE_SYSTEM = (
    "You map monetary mentions in a draft DRHP section to known fact subjects. "
    "You never invent subjects: answer only with subjects from the provided list, or null. "
    'Respond with a single JSON object mapping claim index (as a string) to a subject or null. '
    "No prose."
)


async def _refine_uncited_subjects(
    section: GeneratedSection, claims: list[Claim]
) -> list[Claim]:
    """Optionally re-attribute uncited monetary mentions to known cited subjects.

    Purely a refinement: it may only relabel an uncited claim's subject to one
    that already exists among this section's cited claims. Skipped silently
    whenever the LLM is unavailable or its output cannot be parsed — the
    deterministic claims are always a complete, correct fallback.
    """
    known_subjects = {c.subject for c in claims if not c.subject.startswith("uncited:")}
    uncited = [(i, c) for i, c in enumerate(claims) if c.subject.startswith("uncited:")]
    if not known_subjects or not uncited:
        return claims

    mentions = [
        {
            "index": str(i),
            "text": claim.value,
            "context": section.text[
                max(0, claim.text_span[0] - 80) : claim.text_span[1] + 80
            ],
        }
        for i, claim in uncited
    ]
    user = json.dumps(
        {"known_subjects": sorted(known_subjects), "mentions": mentions}, ensure_ascii=False
    )
    fact_ids = [c.fact_id for c in section.citations]
    try:
        response = await grounded_complete(
            system=_REFINE_SYSTEM, user=user, context_fact_ids=fact_ids, temperature=0.0
        )
        mapping = json.loads(response.text)
        if not isinstance(mapping, dict):
            return claims
    except (LLMUnavailable, NotImplementedError, ValueError, TypeError):
        return claims

    refined = list(claims)
    for i, claim in uncited:
        subject = mapping.get(str(i))
        if isinstance(subject, str) and subject in known_subjects:
            refined[i] = claim.model_copy(update={"subject": subject})
    return refined


async def extract_claims(
    section: GeneratedSection, store: FactStore | None = None
) -> list[Claim]:
    """Extract numeric/entity claims from one generated section.

    Deterministic and primary (works offline, no API key):
    - each citation resolves through *store* to its fact: subject = fact key,
      value = str(fact value), kind = number/entity;
    - Indian-format monetary expressions outside cited spans become claims
      with subject ``"uncited:<entry_id>"``.

    An optional LLM pass may relabel uncited mentions with already-cited
    subjects; on LLMUnavailable it is skipped silently.
    """
    claims: list[Claim] = []
    if store is not None:
        claims.extend(_cited_claims(section, store))
    claims.extend(_uncited_money_claims(section))
    return await _refine_uncited_subjects(section, claims)


# --------------------------------------------------------------------------
# Cross-check
# --------------------------------------------------------------------------


def cross_check(claims: list[Claim]) -> list[Contradiction]:
    """Group claims by subject; flag every group whose normalized values disagree."""
    groups: dict[str, list[Claim]] = {}
    for claim in claims:
        groups.setdefault(claim.subject, []).append(claim)

    contradictions: list[Contradiction] = []
    for subject in sorted(groups):
        group = groups[subject]
        if len(group) < 2:
            continue
        distinct = {normalize(claim.value) for claim in group}
        if len(distinct) > 1:
            contradictions.append(Contradiction(subject=subject, claims=group))
    return contradictions


# --------------------------------------------------------------------------
# Semantic (free-prose) consistency — optional LLM enrichment
# --------------------------------------------------------------------------

_SEMANTIC_SYSTEM = (
    "You review draft IPO offer-document sections for factual consistency. "
    "Find statements in DIFFERENT sections that contradict each other in prose "
    "(not just numbers): conflicting descriptions of the business, conflicting "
    "named entities, incompatible plans. Return STRICT JSON: a list of "
    '{"topic": str, "quotes": [{"entry_id": str, "text": str}]} where every '
    '"text" is a VERBATIM substring copied from that section and every list '
    "has quotes from at least two sections. Return [] when nothing conflicts. "
    "Never paraphrase, never invent text."
)


async def semantic_check(sections: list[GeneratedSection]) -> list[Contradiction]:
    """Cross-section free-prose consistency pass (LLM enrichment only).

    The numeric detector above is the primary, deterministic path. This pass
    catches "section A says X, section B says not-X" conflicts that share no
    fact key. Offline (LLMUnavailable) it returns [] silently — it is an
    enrichment, never a gate. Anti-invention guard: a reported quote survives
    only if it is a verbatim substring of the named section's text.
    """
    with_text = [s for s in sections if s.text.strip()]
    if len(with_text) < 2:
        return []
    payload = json.dumps(
        [{"entry_id": s.entry_id, "text": s.text} for s in with_text], ensure_ascii=False
    )
    fact_ids = [c.fact_id for s in with_text for c in s.citations]
    try:
        response = await grounded_complete(
            system=_SEMANTIC_SYSTEM, user=payload, context_fact_ids=fact_ids, temperature=0.0
        )
        items = json.loads(response.text)
        if not isinstance(items, list):
            return []
    except (LLMUnavailable, NotImplementedError, ValueError, TypeError):
        return []

    by_id = {s.entry_id: s for s in with_text}
    out: list[Contradiction] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        topic = item.get("topic")
        quotes = item.get("quotes")
        if not isinstance(topic, str) or not isinstance(quotes, list):
            continue
        claims: list[Claim] = []
        for q in quotes:
            if not isinstance(q, dict):
                continue
            section = by_id.get(q.get("entry_id", ""))
            text = q.get("text")
            if section is None or not isinstance(text, str):
                continue
            start = section.text.find(text)
            if start < 0:  # not verbatim → invented → dropped
                continue
            claims.append(
                Claim(
                    section_entry_id=section.entry_id,
                    kind="entity",
                    subject=f"semantic:{topic}",
                    value=text,
                    text_span=(start, start + len(text)),
                )
            )
        if len({c.section_entry_id for c in claims}) >= 2:
            out.append(Contradiction(subject=f"semantic:{topic}", claims=claims))
    return out
