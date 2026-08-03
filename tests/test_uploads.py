"""Upload extraction: deterministic label scan, INR parsing, proposal → Fact provenance,
OCR routing for scanned PDFs/images."""

from __future__ import annotations

import asyncio
import io

import fitz
import pytest
from PIL import Image

from app.facts import SourceKind
from app.intake.ocr import is_ocr_available
from app.intake.uploads import (
    _DETERMINISTIC_CONFIDENCE,
    _OCR_CONFIDENCE,
    ExtractionProposal,
    PageText,
    _deterministic_extract,
    _looks_scanned,
    _page_texts,
    _parse_llm_proposals,
    extract_facts,
    parse_inr_to_paise,
    proposal_to_fact,
)
from app.schema.models import Role


def _make_pdf_bytes(width: float = 400, height: float = 200, text: str | None = None) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if text:
        for i, line in enumerate(text.splitlines()):
            page.insert_text((20, 30 + i * 20), line)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_blank_image_bytes(size: tuple[int, int] = (400, 200)) -> bytes:
    image = Image.new("RGB", size, color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


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
    # No document_id passed on the proposal — the fact's link to the inline
    # document viewer stays None too, never fabricated.
    assert fact.provenance.document_id is None


# --------------------------------------------------------------------------
# document_id threading — the inline document-viewer link (app.intake.vault
# via app.main.uploads_extract) carried from proposal through to Fact.
# --------------------------------------------------------------------------


def test_proposal_to_fact_carries_document_id_page_and_source_file() -> None:
    proposal = ExtractionProposal(
        fact_key="issue_size_paise",
        value=14 * 10**9,
        source_file="bank_sanction_letter.pdf",
        page=2,
        snippet="Issue Size: ₹14.00 crore",
        confidence=0.9,
        document_id="doc-abc-123",
    )
    fact = proposal_to_fact(proposal)

    assert fact.provenance.document_id == "doc-abc-123"
    assert fact.provenance.page == 2
    assert fact.provenance.source_file == "bank_sanction_letter.pdf"
    # detail is still the same human-readable rollup — the new fields are
    # additive, not a replacement for it.
    assert fact.provenance.detail == "bank_sanction_letter.pdf p.2"


def test_deterministic_extract_facts_carry_document_id_when_given() -> None:
    body = "Issue Size: ₹14.00 crore\n"
    proposals = asyncio.run(
        extract_facts("prospectus.txt", body.encode("utf-8"), document_id="doc-xyz-789")
    )
    assert proposals, "expected at least one proposal"
    assert all(p.document_id == "doc-xyz-789" for p in proposals)


def test_extract_facts_document_id_defaults_to_none() -> None:
    body = "Issue Size: ₹14.00 crore\n"
    proposals = asyncio.run(extract_facts("prospectus.txt", body.encode("utf-8")))
    assert proposals, "expected at least one proposal"
    assert all(p.document_id is None for p in proposals)


def test_pdf_extraction_carries_document_id_through_native_text_path() -> None:
    pdf_bytes = _make_pdf_bytes(text="Issue Size: Rs 14.00 crore")
    proposals = asyncio.run(
        extract_facts("prospectus.pdf", pdf_bytes, document_id="doc-pdf-1")
    )
    issue_size = [p for p in proposals if p.fact_key == "issue_size_paise"]
    assert len(issue_size) == 1
    assert issue_size[0].document_id == "doc-pdf-1"


def test_llm_proposals_carry_document_id_when_given() -> None:
    page_text = "Issue Size: ₹14.00 crore"
    response = (
        '[{"fact_key": "issue_size_paise", "value": 14000000000,'
        ' "page": 1, "snippet": "Issue Size: ₹14.00 crore", "confidence": 0.9}]'
    )
    proposals = _parse_llm_proposals(
        response,
        1,
        page_text,
        "term_sheet.txt",
        {"issue_size_paise"},
        document_id="doc-llm-1",
    )
    assert len(proposals) == 1
    assert proposals[0].document_id == "doc-llm-1"


# --------------------------------------------------------------------------
# Real PDF extraction — a genuine text-layer PDF, built with PyMuPDF (which
# this app now depends on for OCR page rendering, see app.intake.ocr) rather
# than a mocked/skipped case.
# --------------------------------------------------------------------------


def test_pdf_with_native_text_layer_extracts_without_ocr() -> None:
    pdf_bytes = _make_pdf_bytes(text="Issue Size: Rs 14.00 crore\nSme Exchange: NSE Emerge")
    proposals = asyncio.run(extract_facts("prospectus.pdf", pdf_bytes))

    issue_size = [p for p in proposals if p.fact_key == "issue_size_paise"]
    assert len(issue_size) == 1
    assert issue_size[0].value == 14 * 10**9
    assert issue_size[0].page == 1
    # a native text layer exists — never fall back to OCR confidence for it
    assert issue_size[0].confidence == _DETERMINISTIC_CONFIDENCE


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


# --------------------------------------------------------------------------
# _looks_scanned — the native-text-layer-too-thin heuristic
# --------------------------------------------------------------------------


def test_looks_scanned_true_for_empty_or_near_empty_text() -> None:
    assert _looks_scanned("") is True
    assert _looks_scanned("   \n\n  ") is True
    assert _looks_scanned("a b") is True  # a handful of stray chars — watermark noise


def test_looks_scanned_false_for_real_disclosure_prose() -> None:
    assert _looks_scanned("Issue Size: Rs 14.00 crore") is False


# --------------------------------------------------------------------------
# _page_texts routing: scanned PDF pages and image uploads fall back
# gracefully when OCR isn't available (true in this environment — no
# Tesseract binary — so this exercises the real fallback path, not a mock)
# --------------------------------------------------------------------------


def test_scanned_pdf_page_falls_back_to_empty_text_without_ocr() -> None:
    assert is_ocr_available() is False, "this test asserts the no-OCR fallback path"
    # A page with NO insert_text call at all — genuinely no text layer,
    # the same shape a real scanned/photographed page would have.
    pdf_bytes = _make_pdf_bytes(text=None)
    pages = _page_texts("scanned.pdf", pdf_bytes)
    assert len(pages) == 1
    assert pages[0] == PageText(text="", ocr=False)


def test_scanned_pdf_extraction_yields_no_proposals_not_a_crash() -> None:
    pdf_bytes = _make_pdf_bytes(text=None)
    proposals = asyncio.run(extract_facts("scanned.pdf", pdf_bytes))
    assert proposals == []


def test_image_upload_falls_back_to_empty_text_without_ocr() -> None:
    assert is_ocr_available() is False, "this test asserts the no-OCR fallback path"
    pages = _page_texts("photo.png", _make_blank_image_bytes())
    assert pages == [PageText(text="", ocr=False)]


def test_image_upload_extraction_yields_no_proposals_not_a_crash() -> None:
    proposals = asyncio.run(extract_facts("photo.jpg", _make_blank_image_bytes()))
    assert proposals == []


def test_page_texts_recognises_every_supported_image_extension() -> None:
    for ext in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"):
        pages = _page_texts(f"document{ext}", _make_blank_image_bytes())
        assert len(pages) == 1, f"extension {ext} was not routed as an image"


def test_txt_upload_is_never_routed_through_ocr() -> None:
    pages = _page_texts("plain.txt", b"Issue Size: Rs 14.00 crore")
    assert pages == [PageText(text="Issue Size: Rs 14.00 crore", ocr=False)]


# --------------------------------------------------------------------------
# Confidence downgrade for OCR-sourced text (deterministic + LLM paths)
# --------------------------------------------------------------------------


def test_deterministic_extract_uses_ocr_confidence_for_ocr_pages() -> None:
    pages = [PageText(text="Issue Size: Rs 14.00 crore", ocr=True)]
    proposals = _deterministic_extract(pages, "scanned.pdf", {"issue_size_paise"})
    assert len(proposals) == 1
    assert proposals[0].confidence == _OCR_CONFIDENCE
    assert proposals[0].confidence < _DETERMINISTIC_CONFIDENCE


def test_deterministic_extract_uses_native_confidence_for_native_pages() -> None:
    pages = [PageText(text="Issue Size: Rs 14.00 crore", ocr=False)]
    proposals = _deterministic_extract(pages, "term_sheet.txt", {"issue_size_paise"})
    assert len(proposals) == 1
    assert proposals[0].confidence == _DETERMINISTIC_CONFIDENCE


def test_llm_proposal_confidence_capped_when_source_page_is_ocr() -> None:
    page_text = "Issue Size: Rs 14.00 crore"
    # The model claims high confidence — must still be capped, since it has
    # no way to know its source page came from character recognition.
    response = (
        '[{"fact_key": "issue_size_paise", "value": 14000000000,'
        ' "page": 1, "snippet": "Issue Size: Rs 14.00 crore", "confidence": 0.95}]'
    )
    proposals = _parse_llm_proposals(
        response, 1, page_text, "scanned.pdf", {"issue_size_paise"}, is_ocr=True
    )
    assert len(proposals) == 1
    assert proposals[0].confidence == _OCR_CONFIDENCE


def test_llm_proposal_confidence_uncapped_when_source_page_is_native() -> None:
    page_text = "Issue Size: Rs 14.00 crore"
    response = (
        '[{"fact_key": "issue_size_paise", "value": 14000000000,'
        ' "page": 1, "snippet": "Issue Size: Rs 14.00 crore", "confidence": 0.95}]'
    )
    proposals = _parse_llm_proposals(
        response, 1, page_text, "term_sheet.txt", {"issue_size_paise"}, is_ocr=False
    )
    assert len(proposals) == 1
    assert proposals[0].confidence == 0.95


# --------------------------------------------------------------------------
# Real OCR end-to-end — skipped on machines without a Tesseract install
# (this sandbox included), same pattern as test_ocr.py
# --------------------------------------------------------------------------


@pytest.mark.skipif(not is_ocr_available(), reason="Tesseract OCR is not installed")
def test_real_ocr_extracts_facts_from_a_scanned_looking_pdf_page() -> None:
    from PIL import ImageDraw

    image = Image.new("RGB", (600, 100), color="white")
    ImageDraw.Draw(image).text((10, 40), "Issue Size: Rs 14.00 crore", fill="black")

    # Build a PDF page whose only content is that image — no text layer —
    # the same shape a real scanned page has.
    doc = fitz.open()
    page = doc.new_page(width=600, height=100)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    page.insert_image(page.rect, stream=buf.getvalue())
    pdf_bytes = doc.tobytes()
    doc.close()

    proposals = asyncio.run(extract_facts("scanned_prospectus.pdf", pdf_bytes))
    issue_size = [p for p in proposals if p.fact_key == "issue_size_paise"]
    assert len(issue_size) == 1
    assert issue_size[0].value == 14 * 10**9
    assert issue_size[0].confidence == _OCR_CONFIDENCE
