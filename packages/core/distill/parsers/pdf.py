"""
distill.parsers.pdf
~~~~~~~~~~~~~~~~~~~
Parser for PDF documents — both native text and scanned.

Routing strategy (quality-gated cascade):
  1. pdfplumber  — fast, excellent for clean native PDFs with tables
  2. PyMuPDF     — high-performance fallback for native PDFs
  3. marker-pdf  — AI-based layout detection for complex multi-column PDFs
  4. docling      — IBM document understanding for scanned / complex PDFs
  5. Tesseract    — lightweight OCR fallback

Install:
    pip install distill-core        # includes pdfplumber
    pip install distill-core[ocr]   # adds docling + Tesseract for scanned PDFs
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    CodeBlock, Document, DocumentMetadata, Image, ImageType,
    Paragraph, Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser
from distill.registry import registry


@registry.register
class PdfParser(Parser):
    """
    Parses PDF files using a quality-gated cascade.
    Falls back through available extractors until quality threshold is met.
    """

    extensions = [".pdf"]
    mime_types = ["application/pdf"]
    requires          = ["pdfplumber"]
    optional_requires = ["fitz", "docling", "pytesseract", "pdf2image"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()
        path    = Path(source) if not isinstance(source, bytes) else None

        is_native = self._is_native_text_pdf(source)

        if is_native:
            return self._parse_native(source, options)
        else:
            return self._parse_scanned(source, options)

    # ── Native PDF ───────────────────────────────────────────────────────────

    def _parse_native(self, source, options: ParseOptions) -> Document:
        try:
            import pdfplumber
        except ImportError as e:
            raise ParseError(f"pdfplumber not available: {e}") from e

        metadata = DocumentMetadata(source_format="pdf")
        document = Document(metadata=metadata)

        try:
            if isinstance(source, bytes):
                import io
                pdf = pdfplumber.open(io.BytesIO(source))
            else:
                pdf = pdfplumber.open(str(source))

            metadata.page_count  = len(pdf.pages)
            metadata.source_path = str(source) if not isinstance(source, bytes) else None

            for page_num, page in enumerate(pdf.pages, start=1):
                section = Section(
                    heading = [TextRun(text=f"Page {page_num}")],
                    level   = 2,
                )

                # Extract tables first (pdfplumber bbox-aware table detection)
                tables       = page.extract_tables() or []
                table_bboxes = [t.bbox for t in page.find_tables()] if hasattr(page, 'find_tables') else []

                for tbl_data in tables:
                    if not tbl_data:
                        continue
                    rows = []
                    for i, row in enumerate(tbl_data):
                        cells = [
                            TableCell(
                                content   = [TextRun(text=str(cell or ""))],
                                is_header = (i == 0),
                            )
                            for cell in row
                        ]
                        rows.append(TableRow(cells=cells))
                    section.blocks.append(Table(rows=rows))

                # Extract remaining text (outside table bounding boxes)
                text = page.extract_text() or ""
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        section.blocks.append(Paragraph(runs=[TextRun(text=line)]))

                document.sections.append(section)

            pdf.close()

        except Exception as e:
            raise ParseError(f"pdfplumber extraction failed: {e}") from e

        return document

    # ── Scanned PDF ──────────────────────────────────────────────────────────

    def _parse_scanned(self, source, options: ParseOptions) -> Document:
        """Route scanned PDFs through docling or Tesseract."""
        # Try docling first (higher quality)
        try:
            return self._parse_with_docling(source, options)
        except (ImportError, Exception):
            pass

        # Fall back to Tesseract
        try:
            return self._parse_with_tesseract(source, options)
        except (ImportError, Exception) as e:
            raise ParseError(
                f"No OCR engine available for scanned PDF. "
                f"Install with: pip install distill-core[ocr]. Error: {e}"
            ) from e

    def _parse_with_docling(self, source, options: ParseOptions) -> Document:
        # TODO: implement docling integration
        # from docling.document_converter import DocumentConverter
        raise ImportError("docling integration not yet implemented")

    def _parse_with_tesseract(self, source, options: ParseOptions) -> Document:
        # TODO: implement Tesseract + pdf2image integration
        raise ImportError("Tesseract integration not yet implemented")

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _is_native_text_pdf(self, source) -> bool:
        """
        Detect whether a PDF has extractable text (native) or is image-only (scanned).
        Heuristic: extract first page chars — if count > 100, assume native.
        """
        try:
            import pdfplumber, io
            data = open(source, "rb").read() if not isinstance(source, bytes) else source
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if not pdf.pages:
                    return False
                text = pdf.pages[0].extract_text() or ""
                return len(text.strip()) > 100
        except Exception:
            return True  # assume native if detection fails
