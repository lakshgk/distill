"""
distill.parsers.pdf
~~~~~~~~~~~~~~~~~~~
Parser for PDF documents — both native text and scanned.

Routing strategy (quality-gated cascade):
  1. pdfplumber  — native text PDFs (tables + text, header/footer suppressed)
  2. docling     — scanned / complex PDFs (Phase 4)
  3. Tesseract   — lightweight OCR fallback (Phase 4)

Install:
    pip install distill-core        # includes pdfplumber
    pip install distill-core[ocr]   # adds docling + Tesseract for scanned PDFs
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    Document, DocumentMetadata, Paragraph, Section, Table,
    TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser
from distill.registry import registry


# ── Security constants ────────────────────────────────────────────────────────

_MAX_FILE_BYTES = 50 * 1024 * 1024   # 50 MB input limit


def _check_input_size(source, max_bytes: int) -> None:
    """Raise ParseError if the input exceeds the configured size limit."""
    if isinstance(source, bytes):
        size = len(source)
    else:
        try:
            size = Path(source).stat().st_size
        except OSError:
            return
    if size > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise ParseError(
            f"Input file exceeds the {mb} MB size limit "
            f"({size / 1024 / 1024:.1f} MB). "
            f"Increase via options.extra['max_file_size']."
        )


# ── PDF date parsing ──────────────────────────────────────────────────────────

def _parse_pdf_date(raw: Optional[str]) -> Optional[str]:
    """
    Convert a PDF date string (D:YYYYMMDDHHmmSSOHH'mm') to ISO 8601.
    Returns None if the string cannot be parsed.
    """
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("D:"):
        s = s[2:]
    match = re.match(
        r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?([Z+\-].+)?", s
    )
    if not match:
        return None
    year, month, day, hour, minute = match.group(1, 2, 3, 4, 5)
    second = match.group(6) or "00"
    tz_raw = (match.group(7) or "Z").strip()
    try:
        dt = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
        if tz_raw in ("Z", ""):
            return dt + "+00:00"
        tz_match = re.match(r"([+\-])(\d{2})[':']?(\d{2})'?", tz_raw)
        if tz_match:
            sign, hh, mm = tz_match.groups()
            return f"{dt}{sign}{hh}:{mm}"
        return dt + "+00:00"
    except Exception:
        return None


# ── Metadata extraction ───────────────────────────────────────────────────────

def _extract_metadata(pdf, source) -> DocumentMetadata:
    """
    Build DocumentMetadata from a pdfplumber PDF object.
    All fields are best-effort; missing values stay None / empty.
    """
    meta = DocumentMetadata(
        source_format="pdf",
        source_path=str(source) if not isinstance(source, bytes) else None,
        page_count=len(pdf.pages),
    )
    info = pdf.metadata or {}

    def _str(key: str) -> Optional[str]:
        val = info.get(key)
        if isinstance(val, bytes):
            try:
                val = val.decode("utf-8", errors="replace")
            except Exception:
                return None
        return (val or "").strip() or None

    meta.title       = _str("Title")
    meta.author      = _str("Author")
    meta.subject     = _str("Subject")
    meta.description = _str("Subject")   # PDF has no separate description field
    meta.created_at  = _parse_pdf_date(_str("CreationDate"))
    meta.modified_at = _parse_pdf_date(_str("ModDate"))

    raw_kw = _str("Keywords")
    if raw_kw:
        meta.keywords = [k.strip() for k in re.split(r"[,;]", raw_kw) if k.strip()]

    return meta


# ── Text helpers ──────────────────────────────────────────────────────────────

_PAGE_NUMBER_RE = re.compile(r"^\s*\d+\s*$")


def _extract_page_text(page) -> str:
    """
    Extract text from a page, suppressing:
      - header region (top 5 % of page height)
      - footer region (bottom 8 % of page height)
      - regions covered by detected tables
    """
    h = page.height
    w = page.width

    # Crop to body region (exclude header / footer margins)
    body = page.crop((0, h * 0.05, w, h * 0.92))

    # Exclude table bounding boxes from text extraction
    try:
        tables = body.find_tables()
        for tbl in tables:
            body = body.outside_bbox(tbl.bbox)
    except Exception:
        pass

    return body.extract_text() or ""


def _text_to_paragraphs(text: str) -> list[Paragraph]:
    """Convert a block of plain text into Paragraph IR nodes, skipping page numbers."""
    paragraphs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _PAGE_NUMBER_RE.match(line):
            continue
        paragraphs.append(Paragraph(runs=[TextRun(text=line)]))
    return paragraphs


# ── Table extraction ──────────────────────────────────────────────────────────

def _extract_tables(page, max_rows: int = 500) -> list[Table]:
    """Extract all tables from a page into IR Table nodes."""
    tables: list[Table] = []
    try:
        raw_tables = page.extract_tables() or []
    except Exception:
        return tables

    for tbl_data in raw_tables:
        if not tbl_data:
            continue
        rows: list[TableRow] = []
        for i, row in enumerate(tbl_data):
            if max_rows and i >= max_rows:
                break
            cells = [
                TableCell(
                    content=[Paragraph(runs=[TextRun(text=str(cell or "").strip())])],
                    is_header=(i == 0),
                )
                for cell in row
            ]
            rows.append(TableRow(cells=cells))

        truncated  = max_rows and len(tbl_data) > max_rows
        total_rows = len(tbl_data) if truncated else None
        tables.append(Table(rows=rows, truncated=bool(truncated), total_rows=total_rows))

    return tables


# ── Parser ────────────────────────────────────────────────────────────────────

@registry.register
class PdfParser(Parser):
    """
    Parses PDF files.

    Native PDFs: pdfplumber with table-region filtering and header/footer suppression.
    Scanned PDFs: routed to docling / Tesseract (Phase 4).
    """

    extensions        = [".pdf"]
    mime_types        = ["application/pdf"]
    requires          = ["pdfplumber"]
    optional_requires = ["docling", "pytesseract", "pdf2image"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        try:
            import pdfplumber
        except ImportError as e:
            raise ParseError(f"pdfplumber not available: {e}") from e

        # ── Security: input size check ────────────────────────────────────────
        max_file = options.extra.get("max_file_size", _MAX_FILE_BYTES)
        _check_input_size(source, max_file)

        try:
            if isinstance(source, bytes):
                import io
                pdf = pdfplumber.open(io.BytesIO(source))
            else:
                pdf = pdfplumber.open(str(source))
        except Exception as e:
            msg = str(e).lower()
            if "password" in msg or "encrypted" in msg:
                raise ParseError(
                    "PDF is password-protected. "
                    "Distill cannot process encrypted PDFs."
                ) from e
            raise ParseError(f"Could not open PDF: {e}") from e

        with pdf:
            page_count = len(pdf.pages)
            document   = self._parse_native(pdf, source, options)

            # Quality gate: if native extraction yielded too few words per page,
            # the PDF is likely scanned (image-only) — route to the OCR pipeline.
            # OCR is opt-in: callers must set options.extra['enable_ocr'] = True.
            from distill.parsers._ocr import is_scanned_pdf, ocr_pdf

            if is_scanned_pdf(document, page_count):
                # Determine effective OCR-enabled state: ParseOptions.ocr_enabled
                # takes precedence, then fall back to extra['enable_ocr']
                ocr_on = options.ocr_enabled and options.extra.get("enable_ocr", options.ocr_enabled)

                if ocr_on:
                    document.warnings.append(
                        "Sparse text layer detected — running OCR pipeline"
                    )
                    try:
                        return ocr_pdf(source, options)
                    except ParseError as ocr_err:
                        document.warnings.append(f"OCR not available: {ocr_err}")
                        return document
                else:
                    # Scanned PDF with OCR disabled — signal OCR_REQUIRED
                    from distill import ParserOutcome
                    document.parser_outcome = ParserOutcome.OCR_REQUIRED
                    document.warnings.append(
                        "Sparse text layer detected — this PDF may be scanned. "
                        "Enable OCR in Options for better results."
                    )

            return document

    # ── Native PDF ───────────────────────────────────────────────────────────

    def _parse_native(self, pdf, source, options: ParseOptions) -> Document:
        metadata = _extract_metadata(pdf, source)
        document = Document(metadata=metadata)
        max_rows = options.max_table_rows

        # Capture source word count before IR mapping
        try:
            total_words = 0
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                total_words += len(page_text.split())
            document.metadata.word_count = total_words or None
        except Exception:
            pass

        # Math detection — scan character data for math fonts/symbols
        if options and options.collector:
            try:
                from distill.features.math_detection import MathDetector
                char_data = []
                for page_num_md, page_md in enumerate(pdf.pages, start=1):
                    for char in (page_md.chars or []):
                        char_data.append({
                            "fontname": char.get("fontname", ""),
                            "text": char.get("text", ""),
                            "page_number": page_num_md,
                        })
                if char_data:
                    MathDetector().detect_in_pdf(char_data, options.collector)
            except Exception:
                pass

        for page_num, page in enumerate(pdf.pages, start=1):
            section = Section(
                heading=[TextRun(text=f"Page {page_num}")],
                level=2,
            )

            # Tables first (bbox-aware)
            for table in _extract_tables(page, max_rows=max_rows):
                section.blocks.append(table)

            # Text outside table regions, with header/footer suppression
            text = _extract_page_text(page)
            section.blocks.extend(_text_to_paragraphs(text))

            # Only add section if it has content
            if section.blocks:
                document.sections.append(section)

        return document
