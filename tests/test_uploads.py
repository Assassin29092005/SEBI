"""Upload extraction: deterministic label scan, INR parsing, proposal → Fact provenance."""

from __future__ import annotations

import asyncio

import pytest

from app.facts import SourceKind
from app.intake.uploads import (
    ExtractionProposal,
    _parse_llm_proposals,
    extract_facts,
    parse_inr_to_paise,
    proposal_to_fact,
)
from app.schema.models import Role


# --------------------------------------------------------------------------
# parse_inr_to_paise
# --------------------------------------------------------------------------


def test_parse_inr_crore_marker_and_unit() -> None:
    # ₹14.00 crore -> 14 * 10^7 rupees -> 14 * 10^9 paise
    assert parse_inr_to_paise("₹14.00 crore") == 14 * 10**9


def test_parse_inr_rs_prefix_with_indian_comma_grouping() -> None:
    # Rs. 12,50,00,000 (rupees) -> rupees * 100 = paise
    assert parse_inr_to_paise("Rs. 12,50,00,000") == 12_50_00_000 * 100


def test_parse_inr_lakh_unit() -> None:
    # ₹85 lakh -> 85 * 10^5 rupees -> 85 * 10^7 paise
    assert parse_inr_to_paise("₹85 lakh") == 85 * 10**7


def test_parse_inr_plain_number_no_currency_marker() -> None:
    # The helper's contract: any monetary text with an amount parses; no marker
    # means "rupees" (multiplier = 1 rupee -> *100 paise). This is what the
    # implementation does — parse_inr_to_paise never rejects a bare amount.
    assert parse_inr_to_paise("12345") == 12345 * 100


def test_parse_inr_raises_when_no_number_present() -> None:
    with pytest.raises(ValueError):
        parse_inr_to_paise("no digits here")


# --------------------------------------------------------------------------
# LLM proposals: monetary values are recomputed from the snippet, never
# taken from the model (an LLM 10x-off unit conversion must not survive)
# --------------------------------------------------------------------------


def test_llm_paise_value_is_recomputed_from_snippet_not_trusted() -> None:
    page_text = "Term sheet.\nIssue Size: ₹14.00 crore\nOther prose."
    # Model returns a wrong conversion (10x too big) but a valid snippet.
    response = (
        '[{"fact_key": "issue_size_paise", "value": 140000000000,'
        ' "page": 1, "snippet": "Issue Size: ₹14.00 crore", "confidence": 0.9}]'
    )
    proposals = _parse_llm_proposals(
        response, 1, page_text, "term_sheet.txt", {"issue_size_paise"}
    )
    assert len(proposals) == 1
    assert proposals[0].value == 14 * 10**9  # snippet wins, model arithmetic ignored


def test_llm_paise_proposal_dropped_when_snippet_has_no_amount() -> None:
    page_text = "The issue size will be finalised later."
    response = (
        '[{"fact_key": "issue_size_paise", "value": 14000000000,'
        ' "page": 1, "snippet": "The issue size will be finalised later.",'
        ' "confidence": 0.9}]'
    )
    proposals = _parse_llm_proposals(
        response, 1, page_text, "term_sheet.txt", {"issue_size_paise"}
    )
    assert proposals == []  # no parseable amount in the source text → never propose


# --------------------------------------------------------------------------
# deterministic extract_facts from a UTF-8 txt document
# --------------------------------------------------------------------------


def test_deterministic_extract_facts_from_labeled_txt_and_ignores_noise() -> None:
    # One matching Label: value line (issue size), one prose line with no colon
    # (should be ignored), and one Label: value line with a label that maps to
    # no ontology key (should be dropped).
    body = (
        "Issue Size: ₹14.00 crore\n"
        "This paragraph has no colon so must be ignored as prose.\n"
        "Nonexistent Ontology Label: some value\n"
    )
    proposals = asyncio.run(
        extract_facts("prospectus.txt", body.encode("utf-8"))
    )

    issue_size = [p for p in proposals if p.fact_key == "issue_size_paise"]
    assert len(issue_size) == 1, f"expected 1 issue_size_paise proposal, got {proposals!r}"

    proposal = issue_size[0]
    # _normalise_value("*_paise", "₹14.00 crore") returns int paise:
    assert proposal.value == 14 * 10**9
    assert proposal.source_file == "prospectus.txt"
    assert proposal.page == 1
    assert "Issue Size" in proposal.snippet

    # No ontology label should produce a proposal for the noise/prose lines.
    non_issue_keys = {p.fact_key for p in proposals} - {"issue_size_paise"}
    # We can't assert emptiness (other lines could still map to real keys we
    # didn't intend), but the "Nonexistent Ontology Label" must never appear.
    assert "nonexistent_ontology_label" not in non_issue_keys


# --------------------------------------------------------------------------
# proposal_to_fact provenance
# --------------------------------------------------------------------------


def test_proposal_to_fact_returns_unconfirmed_document_provenance() -> None:
    proposal = ExtractionProposal(
        fact_key="issue_size_paise",
        value=14 * 10**9,
        source_file="prospectus.txt",
        page=3,
        snippet="Issue Size: ₹14.00 crore",
        confidence=0.9,
    )
    fact = proposal_to_fact(proposal)

    assert fact.confirmed is False
    assert fact.provenance.kind is SourceKind.DOCUMENT
    assert "prospectus.txt" in fact.provenance.detail
    assert "p.3" in fact.provenance.detail
    assert fact.provenance.snippet == "Issue Size: ₹14.00 crore"
    assert fact.supplied_by is Role.PROMOTER
    assert fact.key == "issue_size_paise"
    assert fact.value == 14 * 10**9


# --------------------------------------------------------------------------
# PDF smoke test — pypdf.PdfWriter cannot embed a text stream without a
# properly-encoded font, and extract_text() on a manually-built content stream
# reliably returns an empty string. Rather than shipping a brittle assertion,
# state the limitation and skip.
# --------------------------------------------------------------------------


def test_pdf_extraction_smoke_skipped_documented_limitation() -> None:
    pytest.skip(
        "pypdf.PdfWriter has no trivial API for embedding a text content stream "
        "readable by PdfReader.extract_text(); a real PDF smoke test needs a "
        "pre-baked fixture PDF or reportlab. Deterministic path is exercised by "
        "the .txt tests above."
    )


def test_proposal_to_fact_role_tagged_for_auditor() -> None:
    proposal = ExtractionProposal(
        fact_key="issue_size_paise",
        value=14 * 10**9,
        source_file="restated_financials.pdf",
        page=3,
        snippet="Issue Size: Rs 14.00 crore",
        confidence=0.9,
    )
    fact = proposal_to_fact(proposal, supplied_by=Role.AUDITOR)
    assert fact.supplied_by is Role.AUDITOR
    assert not fact.confirmed
