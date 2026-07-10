"""Document upload handling and fact extraction.

Extracted values are *proposals* until the promoter confirms them against the
highlighted source snippet — an unconfirmed fact never feeds generation.

Extraction runs two paths and merges the results:

1. **LLM path** — one ``grounded_complete`` call per page, constrained to the
   fact-ontology keys derived from the checklist schema (never hardcoded).
   The model may only return values literally present in the page text;
   anything ungrounded is dropped at parse time (unknown keys, snippets not
   found verbatim in the page). Monetary (``*_paise``) values are never taken
   from the model — they are recomputed deterministically from the snippet.
2. **Deterministic path** — always runs, and is the sole path when no LLM key
   is configured (offline-first): scans for ``Label: value`` lines using the
   shared label convention.

On a duplicate ``(fact_key, page)`` the LLM proposal wins.
"""

from __future__ import annotations

import io
import json
import logging
import re
from decimal import Decimal

from pydantic import BaseModel
from pypdf import PdfReader

from app.facts import Fact, Provenance, SourceKind
from app.llm.client import grounded_complete
from app.schema.loader import load_checklist
from app.schema.models import Role

try:  # LLMUnavailable ships with the provider implementation; fall back until then.
    from app.llm.client import LLMUnavailable  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - removed once app.llm.client defines it

    class LLMUnavailable(Exception):  # type: ignore[no-redef]
        """Raised when no LLM API key is configured (local fallback definition)."""


logger = logging.getLogger("drhp.intake.uploads")

_DETERMINISTIC_CONFIDENCE = 0.9
_DEFAULT_LLM_CONFIDENCE = 0.5

# crore = 10^7 rupees, lakh = 10^5 rupees; longest spellings first.
_INR_MULTIPLIERS: dict[str, int] = {
    "crores": 10_000_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
    "lakhs": 100_000,
    "lakh": 100_000,
    "lacs": 100_000,
    "lac": 100_000,
}

_AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class ExtractionProposal(BaseModel):
    """A candidate fact awaiting promoter confirmation."""

    fact_key: str
    value: str | int      # int = INR paise for *_paise keys; str otherwise (never floats)
    source_file: str
    page: int
    snippet: str          # highlighted source text shown to the promoter
    confidence: float


def label_for_key(key: str) -> str:
    """Shared label convention: strip ``[]`` and ``_paise``, underscores to spaces, Title Case.

    e.g. ``issue_size_paise`` -> ``"Issue Size"``; ``lead_managers[]`` -> ``"Lead Managers"``.
    """
    base = key.removesuffix("[]").removesuffix("_paise")
    return base.replace("_", " ").title()


def parse_inr_to_paise(text: str) -> int:
    """Parse an Indian-format monetary string to integer paise.

    Handles ``₹``/``Rs.`` markers, Indian comma grouping, and crore/lakh
    multipliers: "₹14.00 crore", "Rs. 12,50,00,000", "₹85 lakh".
    Decimal arithmetic throughout — never floats; sub-paise precision raises.
    """
    lowered = text.lower()
    match = _AMOUNT_RE.search(lowered)
    if match is None:
        raise ValueError(f"no numeric amount found in {text!r}")
    number = Decimal(match.group(0).replace(",", ""))
    multiplier = 1
    for word, factor in _INR_MULTIPLIERS.items():
        if re.search(rf"\b{word}\b", lowered):
            multiplier = factor
            break
    paise = number * multiplier * 100
    if paise != paise.to_integral_value():
        raise ValueError(f"{text!r} does not resolve to a whole number of paise")
    return int(paise)


def _allowed_fact_keys() -> set[str]:
    """Union of required_facts across non-stub checklist entries — the extraction ontology."""
    keys: set[str] = set()
    for entry in load_checklist().entries:
        if not entry.stub:
            keys.update(entry.required_facts)
    return keys


def _page_texts(filename: str, content: bytes) -> list[str]:
    """One text per page: pypdf for .pdf; otherwise utf-8 text split on form-feed."""
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(content))
        return [page.extract_text() or "" for page in reader.pages]
    return content.decode("utf-8", errors="replace").split("\f")


def _normalise_value(fact_key: str, raw: str) -> str | int | None:
    """Coerce per the ontology: *_paise keys become int paise; None means drop the proposal."""
    value = raw.strip()
    if not value:
        return None
    if fact_key.removesuffix("[]").endswith("_paise"):
        try:
            return parse_inr_to_paise(value)
        except ValueError:
            logger.warning("dropping unparseable monetary value for %s: %r", fact_key, value)
            return None
    return value


def _deterministic_extract(
    page_texts: list[str], source_file: str, allowed_keys: set[str]
) -> list[ExtractionProposal]:
    """Scan for ``Label: value`` lines per the shared label convention (offline path)."""
    label_map = {label_for_key(key).lower(): key for key in allowed_keys}
    proposals: list[ExtractionProposal] = []
    for page_num, text in enumerate(page_texts, start=1):
        for line in text.splitlines():
            label, sep, raw_value = line.partition(":")
            if not sep:
                continue  # prose line — ignore
            fact_key = label_map.get(label.strip().lower())
            if fact_key is None:
                continue  # unknown label — drop
            value = _normalise_value(fact_key, raw_value)
            if value is None:
                continue
            proposals.append(
                ExtractionProposal(
                    fact_key=fact_key,
                    value=value,
                    source_file=source_file,
                    page=page_num,
                    snippet=line.strip(),
                    confidence=_DETERMINISTIC_CONFIDENCE,
                )
            )
    return proposals


def _extraction_system_prompt(allowed_keys: set[str]) -> str:
    key_lines = "\n".join(f"- {key}" for key in sorted(allowed_keys))
    return (
        "You extract facts from one page of a document uploaded by an SME IPO issuer.\n"
        "Extract ONLY values literally present in the page text — never infer, never\n"
        "compute, and never fill in from general knowledge.\n"
        "Return a STRICT JSON array (no markdown, no commentary) of objects with keys:\n"
        '"fact_key", "value", "page", "snippet", "confidence".\n'
        '"snippet" must be an exact substring of the page text containing the value.\n'
        '"confidence" is a float between 0 and 1.\n'
        '"fact_key" must be one of the following allowed keys — anything else is '
        "discarded:\n"
        f"{key_lines}"
    )


def _parse_llm_proposals(
    response_text: str,
    page_num: int,
    page_text: str,
    source_file: str,
    allowed_keys: set[str],
) -> list[ExtractionProposal]:
    """Defensive parse: drop anything malformed, unknown, or not grounded in the page."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip())
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        logger.warning("LLM extraction returned no JSON array for page %d", page_num)
        return []
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("LLM extraction returned invalid JSON for page %d", page_num)
        return []
    if not isinstance(items, list):
        return []

    proposals: list[ExtractionProposal] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fact_key = item.get("fact_key")
        raw_value = item.get("value")
        snippet = item.get("snippet")
        if fact_key not in allowed_keys:
            continue  # unknown key — drop
        if not isinstance(snippet, str) or snippet not in page_text:
            continue  # not grounded in the source page — never propose it
        if isinstance(raw_value, bool) or not isinstance(raw_value, str | int | float):
            continue
        value: str | int | None
        if str(fact_key).removesuffix("[]").endswith("_paise"):
            # Never trust LLM arithmetic: the model may return a wrong unit
            # conversion (e.g. 10x off on crore→paise). Recompute the amount
            # from the verified source snippet — the same text the promoter
            # confirms against — and drop the proposal if it doesn't parse.
            try:
                value = parse_inr_to_paise(snippet)
            except ValueError:
                logger.warning(
                    "dropping %s proposal: snippet has no parseable amount: %r",
                    fact_key,
                    snippet,
                )
                continue
        else:
            value = _normalise_value(str(fact_key), str(raw_value))
        if value is None:
            continue
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            confidence = _DEFAULT_LLM_CONFIDENCE
        confidence = min(max(float(confidence), 0.0), 1.0)
        proposals.append(
            ExtractionProposal(
                fact_key=str(fact_key),
                value=value,
                source_file=source_file,
                page=page_num,  # one call per page — trust our page number, not the model's
                snippet=snippet,
                confidence=confidence,
            )
        )
    return proposals


async def _llm_extract(
    page_texts: list[str], source_file: str, allowed_keys: set[str]
) -> list[ExtractionProposal]:
    """One grounded_complete call per page; proposals validated against the page text."""
    system = _extraction_system_prompt(allowed_keys)
    proposals: list[ExtractionProposal] = []
    for page_num, page_text in enumerate(page_texts, start=1):
        if not page_text.strip():
            continue
        response = await grounded_complete(
            system=system,
            user=f"Document: {source_file} — page {page_num}\n\n{page_text}",
            context_fact_ids=[],  # extraction has no fact context yet
            temperature=0.0,
        )
        proposals.extend(
            _parse_llm_proposals(response.text, page_num, page_text, source_file, allowed_keys)
        )
    return proposals


async def extract_facts(filename: str, content: bytes) -> list[ExtractionProposal]:
    """Run LLM/deterministic extraction over an uploaded document.

    Both paths run and merge; the deterministic label scan guarantees an
    offline result (no API key needed), and LLM proposals win on a duplicate
    ``(fact_key, page)``. Every proposal still requires promoter confirmation.
    """
    page_texts = _page_texts(filename, content)
    allowed_keys = _allowed_fact_keys()

    deterministic = _deterministic_extract(page_texts, filename, allowed_keys)
    try:
        llm = await _llm_extract(page_texts, filename, allowed_keys)
    except (LLMUnavailable, NotImplementedError):
        # NotImplementedError covers the pre-implementation provider stubs.
        logger.info("LLM unavailable — deterministic extraction only for %s", filename)
        llm = []

    merged: dict[tuple[str, int], ExtractionProposal] = {
        (p.fact_key, p.page): p for p in deterministic
    }
    merged.update({(p.fact_key, p.page): p for p in llm})  # LLM wins on duplicates
    return sorted(merged.values(), key=lambda p: (p.page, p.fact_key))


def proposal_to_fact(
    proposal: ExtractionProposal, supplied_by: Role = Role.PROMOTER
) -> Fact:
    """Materialise a confirmed proposal as an unconfirmed Fact (confirmation is separate).

    ``supplied_by`` implements role-based truth: an auditor's restated
    financials or a banker's due-diligence certificate enter under that role,
    not silently as promoter content.
    """
    return Fact(
        key=proposal.fact_key,
        value=proposal.value,
        provenance=Provenance(
            kind=SourceKind.DOCUMENT,
            detail=f"{proposal.source_file} p.{proposal.page}",
            snippet=proposal.snippet,
        ),
        confidence=proposal.confidence,
        supplied_by=supplied_by,
    )
