"""OCR (app.intake.ocr): availability detection, PDF-page rendering, real Tesseract execution.

Real-OCR-execution tests are marked ``@pytest.mark.skipif(not is_ocr_available(), ...)`` —
same pattern as ``@pytest.mark.live_llm`` elsewhere in this suite: Tesseract is a
system binary, not a pip package, so CI/dev machines without it skip those cases
while still exercising the (much larger) fallback-path surface for real.
"""

from __future__ import annotations

import io

import fitz
import pytest
from PIL import Image, ImageDraw

from app.config import settings
from app.intake.ocr import (
    OcrUnavailable,
    is_ocr_available,
    ocr_image,
    ocr_image_bytes,
    render_pdf_page_to_image,
    render_pdf_page_with_highlight,
)


@pytest.fixture(autouse=True)
def _clear_availability_cache() -> None:
    """``is_ocr_available`` is process-cached; each test starts with a clean check."""
    is_ocr_available.cache_clear()
    yield
    is_ocr_available.cache_clear()


def _make_pdf_bytes(width: float, height: float, text: str | None = None) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if text:
        page.insert_text((10, height / 2), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_text_image_bytes(text: str, size: tuple[int, int] = (600, 200)) -> bytes:
    image = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(image)
    draw.text((20, size[1] // 2 - 10), text, fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# Availability detection (real — this environment genuinely has no
# Tesseract binary, so this is not a mocked assertion)
# --------------------------------------------------------------------------


def test_is_ocr_available_matches_real_environment_state() -> None:
    # Whatever this machine's true state is, the check must not raise and
    # must be a plain bool.
    assert isinstance(is_ocr_available(), bool)


def test_ocr_image_raises_when_tesseract_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tesseract_cmd", "/definitely/not/a/real/tesseract/binary")
    is_ocr_available.cache_clear()
    image = Image.new("RGB", (100, 50), color="white")
    with pytest.raises(OcrUnavailable):
        ocr_image(image)


def test_ocr_image_bytes_raises_when_tesseract_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tesseract_cmd", "/definitely/not/a/real/tesseract/binary")
    is_ocr_available.cache_clear()
    with pytest.raises(OcrUnavailable):
        ocr_image_bytes(_make_text_image_bytes("hello"))


def test_ocr_image_bytes_raises_on_unopenable_bytes() -> None:
    with pytest.raises(OcrUnavailable):
        ocr_image_bytes(b"this is not an image at all")


def test_a_bad_tesseract_cmd_does_not_stick_after_it_is_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pointing at a bad binary must not disable OCR for the whole process.

    ``_configure_tesseract_cmd`` used to only ever *set* pytesseract's path,
    guarded by ``if settings.tesseract_cmd``. Reverting the setting to blank
    then skipped the guard entirely, leaving pytesseract pinned to the bad
    path — so every later OCR call in the process raised OcrUnavailable.

    It only showed up where ``TESSERACT_CMD`` is unset (CI), because a
    machine with it set had the revert put a good path back by accident.
    """
    before = is_ocr_available()

    monkeypatch.setattr(settings, "tesseract_cmd", "/definitely/not/a/real/tesseract/binary")
    is_ocr_available.cache_clear()
    assert is_ocr_available() is False

    monkeypatch.undo()
    is_ocr_available.cache_clear()
    assert is_ocr_available() is before


# --------------------------------------------------------------------------
# PDF page rendering (PyMuPDF — always available, no Tesseract needed)
# --------------------------------------------------------------------------


def test_render_pdf_page_to_image_produces_expected_pixel_size() -> None:
    # 72pt = 1 inch; at 300 DPI a 200x100pt page renders to roughly 833x417px
    # (MuPDF's own rounding, not ours — allow a couple of pixels of slack).
    pdf_bytes = _make_pdf_bytes(width=200, height=100)
    image = render_pdf_page_to_image(pdf_bytes, page_index=0)
    expected_w, expected_h = 200 * 300 / 72, 100 * 300 / 72
    assert abs(image.size[0] - expected_w) <= 2
    assert abs(image.size[1] - expected_h) <= 2


def test_render_pdf_page_to_image_selects_the_requested_page() -> None:
    doc = fitz.open()
    doc.new_page(width=100, height=100)  # page 0: blank
    page1 = doc.new_page(width=300, height=150)  # page 1: distinct size
    page1.insert_text((10, 75), "page two")
    pdf_bytes = doc.tobytes()
    doc.close()

    image0 = render_pdf_page_to_image(pdf_bytes, page_index=0)
    image1 = render_pdf_page_to_image(pdf_bytes, page_index=1)
    assert image0.size != image1.size


def test_render_pdf_page_to_image_returns_a_real_pil_image() -> None:
    pdf_bytes = _make_pdf_bytes(width=150, height=150)
    image = render_pdf_page_to_image(pdf_bytes, page_index=0)
    assert isinstance(image, Image.Image)
    assert image.mode in ("RGB", "RGBA", "L")


# --------------------------------------------------------------------------
# Highlighted page rendering (inline document viewer — app.main's
# GET /api/uploads/{document_id}/page/{page_number})
# --------------------------------------------------------------------------


def test_highlight_is_actually_drawn_when_snippet_is_found() -> None:
    pdf_bytes = _make_pdf_bytes(width=300, height=150, text="Issue Size: Rs 14.00 crore")
    plain = render_pdf_page_with_highlight(pdf_bytes, page_index=0, snippet=None)
    highlighted = render_pdf_page_with_highlight(
        pdf_bytes, page_index=0, snippet="Issue Size: Rs 14.00 crore"
    )
    assert plain != highlighted
    assert Image.open(io.BytesIO(highlighted)).format == "PNG"


def test_snippet_not_on_the_page_renders_plainly_without_crashing() -> None:
    pdf_bytes = _make_pdf_bytes(width=300, height=150, text="Issue Size: Rs 14.00 crore")
    plain = render_pdf_page_with_highlight(pdf_bytes, page_index=0, snippet=None)
    # A snippet genuinely absent from this page's text layer (e.g. one that
    # actually came from OCR on a *different*, scanned page) must not raise
    # — search_for simply finds nothing, so the highlight pass is a no-op.
    no_match = render_pdf_page_with_highlight(
        pdf_bytes, page_index=0, snippet="this text is not on the page at all"
    )
    assert plain == no_match


def test_empty_snippet_is_treated_the_same_as_none() -> None:
    pdf_bytes = _make_pdf_bytes(width=300, height=150, text="Issue Size: Rs 14.00 crore")
    plain = render_pdf_page_with_highlight(pdf_bytes, page_index=0, snippet=None)
    empty = render_pdf_page_with_highlight(pdf_bytes, page_index=0, snippet="")
    assert plain == empty


def test_out_of_range_page_raises_index_error_not_something_uncaught() -> None:
    pdf_bytes = _make_pdf_bytes(width=200, height=100, text="one page only")
    with pytest.raises(IndexError):
        render_pdf_page_with_highlight(pdf_bytes, page_index=5, snippet=None)
    with pytest.raises(IndexError):
        render_pdf_page_with_highlight(pdf_bytes, page_index=-1, snippet=None)


def test_highlighted_render_returns_raw_png_bytes() -> None:
    pdf_bytes = _make_pdf_bytes(width=200, height=100, text="hello")
    result = render_pdf_page_with_highlight(pdf_bytes, page_index=0, snippet="hello")
    assert isinstance(result, bytes)
    image = Image.open(io.BytesIO(result))
    assert image.format == "PNG"
    expected_w, expected_h = 200 * 300 / 72, 100 * 300 / 72
    assert abs(image.size[0] - expected_w) <= 2
    assert abs(image.size[1] - expected_h) <= 2


# --------------------------------------------------------------------------
# Real OCR execution — skipped on machines without a Tesseract install
# (this sandbox included; verified against the live library's own
# TesseractNotFoundError, not assumed)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not is_ocr_available(), reason="Tesseract OCR is not installed")
def test_real_ocr_reads_rendered_text_from_an_image() -> None:
    image_bytes = _make_text_image_bytes("Issue Size: Rs 14.00 crore")
    text = ocr_image_bytes(image_bytes)
    assert "Issue Size" in text or "14.00" in text  # OCR is not always pixel-perfect


@pytest.mark.skipif(not is_ocr_available(), reason="Tesseract OCR is not installed")
def test_real_ocr_reads_a_rendered_pdf_page() -> None:
    pdf_bytes = _make_pdf_bytes(width=400, height=200, text="SME Exchange: NSE Emerge")
    # Force this page down the OCR path even though it has a native layer,
    # by rendering + OCR-ing directly rather than going through pypdf.
    image = render_pdf_page_to_image(pdf_bytes, page_index=0)
    text = ocr_image(image)
    assert "SME Exchange" in text or "NSE Emerge" in text
