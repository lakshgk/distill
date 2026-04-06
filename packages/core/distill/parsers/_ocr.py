"""
distill.parsers._ocr
~~~~~~~~~~~~~~~~~~~~
OCR pipeline for scanned PDFs.

Two backends are supported, tried in priority order:

    1. docling  — layout-aware (understands tables, columns, headings).
                  Requires: pip install distill-core[ocr]
    2. Tesseract — lightweight fallback.
                  Requires: pip install distill-core[ocr]
                  Also requires Tesseract to be installed on the system.

Public interface
----------------
    is_scanned_pdf(document, page_count) -> bool
    ocr_via_docling(source, options)     -> Document
    ocr_via_tesseract(source, options)   -> Document
    ocr_pdf(source, options)             -> Document   ← call this from PdfParser

The caller (PdfParser) passes the original source (path, Path, or bytes) and
ParseOptions.  All temp file management is handled internally.

Backend selection
-----------------
The backend is chosen automatically based on what is installed.  Override with:
    options.extra['ocr_backend'] = 'docling'   # force docling
    options.extra['ocr_backend'] = 'tesseract' # force tesseract
"""

from __future__ import annotations

import contextlib
import io
import logging
import shutil
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    CodeBlock, Document, DocumentMetadata,
    List, ListItem, Paragraph, Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions


_logger = logging.getLogger(__name__)

_HF_WARNING_MARKERS = ("hf_token", "huggingface", "unauthenticated", "rate limit")


@contextlib.contextmanager
def _suppress_hf_warnings():
    """Redirect Hugging Face token warnings from stderr to DEBUG logging.

    Non-matching stderr lines are re-emitted to the original stderr so that
    genuine errors are never silenced.  Python ``warnings`` from the
    ``transformers`` and ``huggingface_hub`` packages are also suppressed for
    the duration of the block.
    """
    original_stderr = sys.stderr
    original_filters = warnings.filters[:]
    buf = io.StringIO()
    try:
        sys.stderr = buf
        warnings.filterwarnings("ignore", module=r"transformers.*")
        warnings.filterwarnings("ignore", module=r"huggingface_hub.*")
        yield
    finally:
        sys.stderr = original_stderr
        warnings.filters[:] = original_filters

        captured = buf.getvalue()
        for line in captured.splitlines(keepends=True):
            lower = line.lower()
            if any(marker in lower for marker in _HF_WARNING_MARKERS):
                _logger.debug("Suppressed HF warning: %s", line.rstrip())
            else:
                original_stderr.write(line)


# ── Scanned detection ────────────────────────────────────────────────────────

def is_scanned_pdf(
    document: Document,
    page_count: int,
    min_words_per_page: float = 5.0,
) -> bool:
    """
    Return True if the document looks like a scanned (image-only) PDF.

    Heuristic: if the average word count per page falls below
    *min_words_per_page* the PDF most likely lacks a usable text layer.

    Parameters
    ----------
    document:
        The IR Document produced by native pdfplumber extraction.
    page_count:
        Number of pages in the source PDF.
    min_words_per_page:
        Threshold. Default 5 words/page.
    """
    if page_count <= 0:
        return False

    word_count = 0
    for section in document.sections:
        for block in section.blocks:
            if isinstance(block, Paragraph):
                for run in block.runs:
                    word_count += len(run.text.split())
            elif isinstance(block, Table):
                for row in block.rows:
                    for cell in row.cells:
                        for cell_block in cell.content:
                            if isinstance(cell_block, Paragraph):
                                for run in cell_block.runs:
                                    word_count += len(run.text.split())

    return (word_count / page_count) < min_words_per_page


# ── Temp file helpers ─────────────────────────────────────────────────────────

def _source_to_path(
    source: Union[str, Path, bytes],
    suffix: str = ".pdf",
) -> tuple[Path, bool]:
    """
    Return (path, created) where *created* is True if a temp file was written.
    Caller must delete the temp file when created=True.
    """
    if isinstance(source, bytes):
        tmp = Path(tempfile.mktemp(suffix=suffix))
        tmp.write_bytes(source)
        return tmp, True
    return Path(source), False


# ── docling backend ───────────────────────────────────────────────────────────

def ocr_via_docling(
    source: Union[str, Path, bytes],
    options: Optional[ParseOptions] = None,
) -> Document:
    """
    Convert a scanned PDF to an IR Document using docling.

    docling performs layout-aware OCR: it detects headings, paragraphs, tables,
    lists, and code blocks and returns a structured document model.

    Raises
    ------
    ParseError
        - docling is not installed
        - conversion fails
    """
    options = options or ParseOptions()

    try:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.document import ConversionResult
    except ImportError as exc:
        raise ParseError(
            f"docling is not installed: {exc}. "
            "Install with: pip install distill-core[ocr]"
        ) from exc

    src_path, created = _source_to_path(source)
    try:
        try:
            with _suppress_hf_warnings():
                converter = DocumentConverter()
                result    = converter.convert(str(src_path))
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"docling conversion failed: {exc}") from exc

        return _docling_result_to_ir(result, source)
    finally:
        if created:
            src_path.unlink(missing_ok=True)


def _docling_result_to_ir(result, source: Union[str, Path, bytes]) -> Document:
    """
    Map a docling ``ConversionResult`` to a Distill IR ``Document``.

    docling item labels used:
        title, section_header → Section headings
        text                  → Paragraph
        list_item             → ListItem (collected into List blocks)
        table                 → Table
        code, formula         → CodeBlock
        picture               → suppressed (no image extraction in OCR path)
    """
    doc = Document(
        metadata=DocumentMetadata(
            source_format="pdf",
            source_path=str(source) if not isinstance(source, bytes) else None,
        ),
        warnings=["OCR performed by docling — content is reconstructed from image"],
    )

    # docling's document model
    dl_doc = result.document

    # We walk the top-level items and group consecutive list_items into Lists.
    # A fresh section is created each time a title/section_header is encountered.
    current_section = Section(level=0, blocks=[])
    pending_list_items: list[ListItem] = []

    def _flush_list():
        nonlocal pending_list_items
        if pending_list_items:
            current_section.blocks.append(List(items=pending_list_items))
            pending_list_items = []

    def _text_of(item) -> str:
        try:
            return (item.text or "").strip()
        except AttributeError:
            return ""

    try:
        items = list(dl_doc.iterate_items())
    except Exception:
        # Fallback: export markdown and split into paragraphs
        try:
            md = dl_doc.export_to_markdown()
        except Exception:
            md = ""
        doc.sections.append(
            Section(
                level=0,
                blocks=[
                    Paragraph(runs=[TextRun(text=line)])
                    for line in md.splitlines()
                    if line.strip()
                ],
            )
        )
        return doc

    for item_tuple in items:
        # docling >= 2 returns (item, level); earlier versions return just item
        if isinstance(item_tuple, tuple):
            item = item_tuple[0]
        else:
            item = item_tuple

        label = getattr(getattr(item, "label", None), "value", None) or str(
            getattr(item, "label", "")
        )
        text  = _text_of(item)

        if label in ("title", "section_header"):
            _flush_list()
            # Commit the current section if it has content
            if current_section.blocks or current_section.heading:
                doc.sections.append(current_section)
            level = 1 if label == "title" else 2
            current_section = Section(
                heading=[TextRun(text=text)],
                level=level,
                blocks=[],
            )

        elif label in ("text", "paragraph"):
            _flush_list()
            if text:
                current_section.blocks.append(
                    Paragraph(runs=[TextRun(text=text)])
                )

        elif label == "list_item":
            if text:
                pending_list_items.append(ListItem(content=[TextRun(text=text)]))

        elif label in ("code", "formula"):
            _flush_list()
            if text:
                current_section.blocks.append(CodeBlock(code=text))

        elif label == "table":
            _flush_list()
            tbl = _docling_table_to_ir(item, options=ParseOptions())
            if tbl:
                current_section.blocks.append(tbl)

        # picture / caption / other: skipped

    _flush_list()
    if current_section.blocks or current_section.heading:
        doc.sections.append(current_section)

    return doc


def _docling_table_to_ir(
    item,
    options: ParseOptions,
) -> Optional[Table]:
    """Convert a docling TableItem to an IR Table."""
    try:
        # docling TableItem exposes .data.grid: list[list[TableCell]]
        grid = item.data.grid
        if not grid:
            return None

        rows: list[TableRow] = []
        max_rows = options.max_table_rows or 0

        for row_idx, row in enumerate(grid):
            if max_rows and row_idx >= max_rows:
                break
            cells = [
                TableCell(
                    content=[Paragraph(runs=[TextRun(text=(cell.text or "").strip())])],
                    colspan=getattr(cell, "col_span", 1) or 1,
                    rowspan=getattr(cell, "row_span", 1) or 1,
                    is_header=(row_idx == 0),
                )
                for cell in row
            ]
            rows.append(TableRow(cells=cells))

        truncated  = bool(max_rows and len(grid) > max_rows)
        total_rows = len(grid) if truncated else None
        return Table(rows=rows, truncated=truncated, total_rows=total_rows)

    except Exception:
        return None


# ── Tesseract backend ─────────────────────────────────────────────────────────

def ocr_via_tesseract(
    source: Union[str, Path, bytes],
    options: Optional[ParseOptions] = None,
) -> Document:
    """
    Convert a scanned PDF to an IR Document using pytesseract + pdf2image.

    Each PDF page is rasterised to an image (300 DPI by default) and passed
    to Tesseract for text extraction.  The result is a flat sequence of
    page Sections containing Paragraphs.

    Raises
    ------
    ParseError
        - pytesseract or pdf2image is not installed
        - Tesseract binary is not on PATH
        - conversion fails
    """
    options = options or ParseOptions()
    dpi     = options.extra.get("ocr_dpi", 300)
    lang    = options.extra.get("ocr_lang", "eng")

    try:
        import pytesseract
        from pdf2image import convert_from_bytes, convert_from_path
    except ImportError as exc:
        raise ParseError(
            f"pytesseract / pdf2image not installed: {exc}. "
            "Install with: pip install distill-core[ocr]"
        ) from exc

    # Verify Tesseract binary is available
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise ParseError(
            f"Tesseract OCR binary not found: {exc}. "
            "Install Tesseract (https://tesseract-ocr.github.io/tessdoc/Installation.html) "
            "and ensure 'tesseract' is on your PATH."
        ) from exc

    # Rasterise pages
    try:
        if isinstance(source, bytes):
            pages = convert_from_bytes(source, dpi=dpi)
        else:
            pages = convert_from_path(str(source), dpi=dpi)
    except Exception as exc:
        raise ParseError(f"pdf2image rasterisation failed: {exc}") from exc

    doc = Document(
        metadata=DocumentMetadata(
            source_format="pdf",
            source_path=str(source) if not isinstance(source, bytes) else None,
            page_count=len(pages),
        ),
        warnings=["OCR performed by Tesseract — content is reconstructed from image"],
    )

    for page_num, image in enumerate(pages, start=1):
        try:
            raw_text: str = pytesseract.image_to_string(image, lang=lang)
        except Exception as exc:
            raw_text = ""
            doc.warnings.append(f"Tesseract failed on page {page_num}: {exc}")

        blocks = _text_to_blocks(raw_text)
        if blocks:
            section = Section(
                heading=[TextRun(text=f"Page {page_num}")],
                level=2,
                blocks=blocks,
            )
            doc.sections.append(section)

    return doc


def _text_to_blocks(text: str) -> list[Paragraph]:
    """
    Split a block of plain OCR text into Paragraph IR nodes.

    Blank lines are treated as paragraph separators.
    Single-line "paragraphs" that are lone page numbers are dropped.
    """
    import re
    _PAGE_NUM_RE = re.compile(r"^\s*\d+\s*$")

    paragraphs: list[Paragraph] = []
    current_lines: list[str] = []

    def _flush():
        joined = " ".join(current_lines).strip()
        if joined and not _PAGE_NUM_RE.match(joined):
            paragraphs.append(Paragraph(runs=[TextRun(text=joined)]))
        current_lines.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            current_lines.append(stripped)
        else:
            _flush()

    _flush()
    return paragraphs


# ── Public entry point ────────────────────────────────────────────────────────

def ocr_pdf(
    source: Union[str, Path, bytes],
    options: Optional[ParseOptions] = None,
) -> Document:
    """
    OCR a scanned PDF and return an IR Document.

    Backend selection (in order):
    1. Respect ``options.extra['ocr_backend']`` if set (``"docling"`` or
       ``"tesseract"``).
    2. Try docling if available.
    3. Fall back to Tesseract if docling is unavailable.
    4. Raise ``ParseError`` if neither backend can run.

    Parameters
    ----------
    source:
        File path, Path object, or raw bytes of the PDF.
    options:
        ParseOptions controlling OCR behaviour.  Relevant extra keys:
          - ``ocr_backend``: ``"docling"`` or ``"tesseract"``
          - ``ocr_dpi``: rasterisation DPI for Tesseract (default 300)
          - ``ocr_lang``: Tesseract language code (default ``"eng"``)
    """
    options = options or ParseOptions()
    forced  = options.extra.get("ocr_backend", "").lower()

    if forced == "docling":
        return ocr_via_docling(source, options)

    if forced == "tesseract":
        return ocr_via_tesseract(source, options)

    # Auto-select: try docling, fall back to tesseract
    try:
        return ocr_via_docling(source, options)
    except ParseError as docling_err:
        if "not installed" not in str(docling_err).lower() and \
           "not available" not in str(docling_err).lower():
            # docling IS installed but failed on this file — propagate
            raise

    try:
        return ocr_via_tesseract(source, options)
    except ParseError as tesseract_err:
        if "not installed" not in str(tesseract_err).lower() and \
           "not available" not in str(tesseract_err).lower() and \
           "not found" not in str(tesseract_err).lower():
            raise

    raise ParseError(
        "Scanned PDF detected but no OCR backend is available. "
        "Install at least one backend:\n"
        "  • pip install distill-core[ocr]  (installs docling + Tesseract)\n"
        "  • Tesseract system binary: https://tesseract-ocr.github.io/"
    )
