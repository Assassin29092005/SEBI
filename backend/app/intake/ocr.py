"""OCR for scanned/photographed documents.

Real SME paperwork often arrives as a phone photo or a flatbed-scanned PDF
with no text layer at all — pypdf's ``extract_text()`` (and a bare ``.txt``
read) only work when the document already carries machine-readable text.
This module fills that gap: Tesseract OCR (via ``pytesseract``) run over
either a standalone image upload or an individual PDF page whose native
text layer is empty/near-empty (rendered to an image first via PyMuPDF, so
no external ``poppler`` install is needed — just the one Tesseract binary).

Optional, like every other real external capability in this app (LLM
providers, the litigation API): ``is_ocr_available()`` checks once per
process whether a working Tesseract binary can actually be found, and every
caller falls back to whatever native extraction produced (often nothing,
for a genuinely scanned page) rather than crashing. Tesseract is a system
binary, not a pip package — it does not ship with this repo and must be
installed separately on the machine running the backend (see README).

Facts extracted from an OCR'd page carry a lower confidence than
native-text extraction (character recognition can misread a digit); the
adversarial examiner already has a low-confidence objection that picks this
up with no changes needed there (see app.intake.uploads._OCR_CONFIDENCE).
"""

from __future__ import annotations

import functools
import logging
from io import BytesIO

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from app.config import settings

logger = logging.getLogger("drhp.intake.ocr")

# PDF points are 1/72 inch; render at a DPI that keeps small print legible
# without producing an unreasonably large bitmap for Tesseract to chew on.
_RENDER_DPI = 300


class OcrUnavailable(Exception):
    """No usable Tesseract installation, or the OCR call itself failed.

    Callers catch this and fall back to whatever native text extraction
    produced — mirrors ``LLMUnavailable`` / ``LitigationUnavailable``.
    """


def _configure_tesseract_cmd() -> None:
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


@functools.lru_cache(maxsize=1)
def is_ocr_available() -> bool:
    """True iff a working Tesseract binary is reachable. Cached per process.

    ``lru_cache`` means a Tesseract install that appears (or a
    ``settings.tesseract_cmd`` change) after this has already been called
    once won't be picked up without ``is_ocr_available.cache_clear()`` —
    tests that monkeypatch either must call that explicitly.
    """
    _configure_tesseract_cmd()
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pytesseract.TesseractNotFoundError, or any OS-level failure
        logger.info("Tesseract OCR not available: %s", exc)
        return False
    return True


def ocr_image(image: Image.Image) -> str:
    """Run Tesseract over a single image; raises ``OcrUnavailable`` on any failure."""
    if not is_ocr_available():
        raise OcrUnavailable("no working Tesseract installation found")
    _configure_tesseract_cmd()
    try:
        # Grayscale is a cheap, well-established accuracy improvement for
        # Tesseract over a raw colour photo — it removes chroma noise the
        # OCR engine would otherwise have to work around.
        return pytesseract.image_to_string(image.convert("L"), lang=settings.tesseract_lang)
    except Exception as exc:
        raise OcrUnavailable(f"Tesseract OCR failed: {exc}") from exc


def ocr_image_bytes(content: bytes) -> str:
    """Open arbitrary image bytes (PNG/JPEG/TIFF/BMP/WEBP/...) and OCR them."""
    try:
        image = Image.open(BytesIO(content))
    except Exception as exc:
        raise OcrUnavailable(f"could not open image for OCR: {exc}") from exc
    return ocr_image(image)


def render_pdf_page_to_image(content: bytes, page_index: int) -> Image.Image:
    """Render one page (0-indexed) of a PDF to a PIL Image via PyMuPDF."""
    with fitz.open(stream=content, filetype="pdf") as doc:
        page = doc[page_index]
        zoom = _RENDER_DPI / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return Image.open(BytesIO(pixmap.tobytes("png")))


def render_pdf_page_with_highlight(content: bytes, page_index: int, snippet: str | None) -> bytes:
    """Render one page (0-indexed) of a PDF to PNG bytes, highlighting every
    occurrence of ``snippet`` found on it.

    The inline document-viewer surface behind fact confirmation: a promoter
    or banker gets the real page, not just a quoted snippet, with the exact
    span highlighted when it can be found. ``page.search_for`` only matches
    text present in the PDF's own embedded text layer — a scanned/
    photographed page has none (that's exactly why OCR exists for it), so a
    snippet sourced from OCR simply won't highlight here; the page still
    renders correctly, just without the overlay. That is a real, honest
    limitation, not a bug: pixel-precise highlighting of OCR'd text would
    need Tesseract's own per-word bounding boxes, a separate, bigger
    capability this function doesn't attempt.

    Raises ``IndexError`` for an out-of-range ``page_index`` — callers
    (see app.main) turn that into a 404, not a 500.
    """
    with fitz.open(stream=content, filetype="pdf") as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(
                f"page index {page_index} out of range (doc has {doc.page_count} pages)"
            )
        page = doc[page_index]
        if snippet:
            for rect in page.search_for(snippet):
                page.add_highlight_annot(rect)
        zoom = _RENDER_DPI / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pixmap.tobytes("png")
