"""
Tests for distill.parsers._ocr and the scanned-PDF gate in PdfParser.

All docling, pytesseract, and pdf2image calls are mocked — no OCR libraries
or Tesseract binary are required to run these tests.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from distill.ir import (
    Document, DocumentMetadata, List, ListItem,
    Paragraph, Section, Table, TextRun, CodeBlock,
)
from distill.parsers.base import ParseError, ParseOptions
from distill.parsers._ocr import (
    _suppress_hf_warnings,
    _text_to_blocks,
    is_scanned_pdf,
    ocr_pdf,
    ocr_via_docling,
    ocr_via_tesseract,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pdf_bytes() -> bytes:
    """Minimal real PDF bytes (no text layer) for integration tests."""
    try:
        from fpdf import FPDF  # type: ignore
        pdf = FPDF()
        pdf.add_page()
        return pdf.output(dest="S").encode("latin-1")
    except Exception:
        # Minimal hand-crafted PDF skeleton (1 blank page, no text stream)
        return b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<</Size 4/Root 1 0 R>>
startxref
190
%%EOF"""


def _doc_with_words(n: int, pages: int = 1) -> Document:
    """Return a Document whose paragraphs contain exactly *n* words total."""
    text = " ".join(["word"] * n)
    return Document(sections=[
        Section(blocks=[Paragraph(runs=[TextRun(text=text)])])
    ])


# ── _suppress_hf_warnings ────────────────────────────────────────────────────

class TestSuppressHfWarnings:
    def test_filters_hf_token_from_stderr(self):
        """Lines containing HF_TOKEN are suppressed from stderr."""
        buf = io.StringIO()
        original = sys.stderr
        sys.stderr = buf
        try:
            with _suppress_hf_warnings():
                print("Set HF_TOKEN to access gated models", file=sys.stderr)
            sys.stderr = original
            assert "HF_TOKEN" not in buf.getvalue()
        finally:
            sys.stderr = original

    def test_filters_huggingface_from_stderr(self):
        """Lines containing 'huggingface' are suppressed from stderr."""
        buf = io.StringIO()
        original = sys.stderr
        sys.stderr = buf
        try:
            with _suppress_hf_warnings():
                print("huggingface unauthenticated request", file=sys.stderr)
            sys.stderr = original
            assert "huggingface" not in buf.getvalue()
        finally:
            sys.stderr = original

    def test_does_not_suppress_genuine_errors(self):
        """Non-HF stderr lines pass through unchanged."""
        buf = io.StringIO()
        original = sys.stderr
        sys.stderr = buf
        try:
            with _suppress_hf_warnings():
                print("genuine error from docling", file=sys.stderr)
            sys.stderr = original
            assert "genuine error" in buf.getvalue()
        finally:
            sys.stderr = original

    def test_restores_stderr_on_normal_exit(self):
        """sys.stderr is restored after the context manager exits normally."""
        original = sys.stderr
        with _suppress_hf_warnings():
            pass
        assert sys.stderr is original

    def test_restores_stderr_on_exception(self):
        """sys.stderr is restored even when an exception is raised inside the block."""
        original = sys.stderr
        with pytest.raises(ValueError, match="test"):
            with _suppress_hf_warnings():
                raise ValueError("test")
        assert sys.stderr is original


# ── is_scanned_pdf ────────────────────────────────────────────────────────────

class TestIsScannedPdf:
    def test_empty_document_is_scanned(self):
        assert is_scanned_pdf(Document(), page_count=1) is True

    def test_zero_pages_always_false(self):
        assert is_scanned_pdf(Document(), page_count=0) is False

    def test_below_threshold_is_scanned(self):
        # 4 words / 1 page < 5 → scanned
        doc = _doc_with_words(4, pages=1)
        assert is_scanned_pdf(doc, page_count=1) is True

    def test_at_threshold_is_not_scanned(self):
        # 5 words / 1 page == 5 → not scanned (threshold is strictly <)
        doc = _doc_with_words(5, pages=1)
        assert is_scanned_pdf(doc, page_count=1) is False

    def test_above_threshold_is_not_scanned(self):
        doc = _doc_with_words(100, pages=5)
        assert is_scanned_pdf(doc, page_count=5) is False

    def test_custom_threshold(self):
        doc = _doc_with_words(10, pages=1)
        # 10 words / 1 page with threshold=15 → scanned
        assert is_scanned_pdf(doc, page_count=1, min_words_per_page=15) is True
        # Same doc with threshold=5 → not scanned
        assert is_scanned_pdf(doc, page_count=1, min_words_per_page=5) is False

    def test_multi_section_word_count(self):
        doc = Document(sections=[
            Section(blocks=[Paragraph(runs=[TextRun(text="one two three")])]),
            Section(blocks=[Paragraph(runs=[TextRun(text="four five six seven eight nine ten")])]),
        ])
        # 10 words / 2 pages = 5 → not scanned
        assert is_scanned_pdf(doc, page_count=2) is False

    def test_non_paragraph_blocks_not_counted(self):
        from distill.ir import Table, TableRow, TableCell
        doc = Document(sections=[
            Section(blocks=[
                Table(rows=[TableRow(cells=[TableCell(content=[Paragraph(runs=[TextRun(text="a")])])])]),
            ])
        ])
        # Table content is not counted — doc appears scanned
        assert is_scanned_pdf(doc, page_count=1) is True


# ── _text_to_blocks ───────────────────────────────────────────────────────────

class TestTextToBlocks:
    def test_single_paragraph(self):
        blocks = _text_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0].runs[0].text == "Hello world"

    def test_blank_line_separates_paragraphs(self):
        blocks = _text_to_blocks("First paragraph\n\nSecond paragraph")
        assert len(blocks) == 2

    def test_page_numbers_dropped(self):
        blocks = _text_to_blocks("Hello\n\n42\n\nWorld")
        texts = [b.runs[0].text for b in blocks]
        assert "42" not in texts
        assert "Hello" in texts
        assert "World" in texts

    def test_empty_string_returns_empty(self):
        assert _text_to_blocks("") == []

    def test_whitespace_only_returns_empty(self):
        assert _text_to_blocks("   \n   \n   ") == []

    def test_lines_in_same_para_joined(self):
        blocks = _text_to_blocks("Line one\nLine two\nLine three")
        assert len(blocks) == 1
        assert "Line one" in blocks[0].runs[0].text
        assert "Line three" in blocks[0].runs[0].text


# ── ocr_via_docling ───────────────────────────────────────────────────────────

def _make_docling_stubs(items=None):
    """
    Return sys.modules patches that provide minimal docling stubs.
    items: list of (label_value, text) tuples used as document items.
    """
    if items is None:
        items = [
            ("section_header", "Introduction"),
            ("text", "This is OCR'd text from the scanned page."),
            ("list_item", "First bullet point"),
            ("list_item", "Second bullet point"),
        ]

    # Build fake item objects
    fake_items = []
    for label_val, text_val in items:
        item = MagicMock()
        item.label.value = label_val
        item.text = text_val
        fake_items.append((item, 0))  # (item, level) tuple

    # Fake document
    fake_doc = MagicMock()
    fake_doc.iterate_items.return_value = fake_items
    fake_doc.export_to_markdown.return_value = "# Heading\n\nSome text"

    # Fake result
    fake_result = MagicMock()
    fake_result.document = fake_doc

    # Fake converter
    mock_converter_cls = MagicMock()
    mock_converter_cls.return_value.convert.return_value = fake_result

    mock_docling              = MagicMock()
    mock_docling_converter    = MagicMock()
    mock_docling_datamodel    = MagicMock()
    mock_docling_base         = MagicMock()
    mock_docling_doc          = MagicMock()

    mock_docling_converter.DocumentConverter = mock_converter_cls

    return {
        "docling": mock_docling,
        "docling.document_converter": mock_docling_converter,
        "docling.datamodel": mock_docling_datamodel,
        "docling.datamodel.base_models": mock_docling_base,
        "docling.datamodel.document": mock_docling_doc,
    }, {
        "converter_cls": mock_converter_cls,
        "result": fake_result,
        "document": fake_doc,
    }


class TestOcrViaDocling:
    def test_returns_document(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_docling_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        assert isinstance(doc, Document)

    def test_section_header_creates_section(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_docling_stubs([
            ("section_header", "My Heading"),
            ("text", "Body text."),
        ])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        headings = [
            " ".join(r.text for r in s.heading)
            for s in doc.sections if s.heading
        ]
        assert "My Heading" in headings

    def test_list_items_collected_into_list_block(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_docling_stubs([
            ("list_item", "Alpha"),
            ("list_item", "Beta"),
            ("list_item", "Gamma"),
        ])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        list_blocks = [
            b for s in doc.sections for b in s.blocks
            if isinstance(b, List)
        ]
        assert len(list_blocks) == 1
        assert len(list_blocks[0].items) == 3

    def test_code_block_mapped(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_docling_stubs([("code", "print('hello')")])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        code_blocks = [
            b for s in doc.sections for b in s.blocks
            if isinstance(b, CodeBlock)
        ]
        assert len(code_blocks) == 1
        assert "print" in code_blocks[0].code

    def test_source_format_set_to_pdf(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, _ = _make_docling_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        assert doc.metadata.source_format == "pdf"

    def test_source_path_preserved(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, _ = _make_docling_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        assert doc.metadata.source_path == str(src)

    def test_source_path_none_for_bytes(self):
        stubs, refs = _make_docling_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(b"fake pdf bytes")

        assert doc.metadata.source_path is None

    def test_warnings_mention_ocr(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, _ = _make_docling_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        assert any("docling" in w.lower() or "ocr" in w.lower()
                   for w in doc.warnings)

    def test_raises_when_not_installed(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"x")

        with patch.dict(sys.modules, {"docling": None,
                                       "docling.document_converter": None,
                                       "docling.datamodel.base_models": None,
                                       "docling.datamodel.document": None}):
            with pytest.raises(ParseError, match="not installed"):
                ocr_via_docling(src)

    def test_fallback_to_markdown_when_iterate_fails(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_docling_stubs()
        # Make iterate_items raise, forcing export_to_markdown fallback
        refs["document"].iterate_items.side_effect = AttributeError("no iterate")

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_docling(src)

        # Should still return a Document (from markdown fallback)
        assert isinstance(doc, Document)

    def test_bytes_input_written_to_temp_file_then_cleaned(self):
        stubs, _ = _make_docling_stubs()
        created_paths = []
        original_mktemp = __import__("tempfile").mktemp

        def capture_mktemp(**kwargs):
            p = original_mktemp(**kwargs)
            created_paths.append(p)
            return p

        with patch.dict(sys.modules, stubs), \
             patch("distill.parsers._ocr.tempfile.mktemp", side_effect=capture_mktemp):
            ocr_via_docling(b"fake pdf bytes")

        # All created temp files should be cleaned up
        for p in created_paths:
            assert not Path(p).exists(), f"Temp file {p} was not cleaned up"


# ── ocr_via_tesseract ─────────────────────────────────────────────────────────

def _make_tesseract_stubs(page_texts=None):
    """
    Build sys.modules stubs for pytesseract + pdf2image.
    page_texts: list of strings, one per page.
    """
    if page_texts is None:
        page_texts = [
            "This is OCR text from page one.\n\nAnother paragraph.",
            "Page two content here.",
        ]

    mock_pytesseract  = MagicMock()
    mock_pdf2image    = MagicMock()
    mock_pil_image    = MagicMock()

    # Version check
    mock_pytesseract.get_tesseract_version.return_value = "5.0.0"

    # image_to_string: cycle through page_texts
    call_count = [0]
    def image_to_string(img, lang="eng"):
        text = page_texts[call_count[0] % len(page_texts)]
        call_count[0] += 1
        return text
    mock_pytesseract.image_to_string.side_effect = image_to_string

    # convert_from_path / convert_from_bytes: return fake image objects
    fake_images = [MagicMock() for _ in page_texts]
    mock_pdf2image.convert_from_path.return_value = fake_images
    mock_pdf2image.convert_from_bytes.return_value = fake_images

    return {
        "pytesseract": mock_pytesseract,
        "pdf2image": mock_pdf2image,
        "PIL": mock_pil_image,
        "PIL.Image": MagicMock(),
    }, {
        "pytesseract": mock_pytesseract,
        "pdf2image": mock_pdf2image,
        "images": fake_images,
    }


class TestOcrViaTesseract:
    def test_returns_document(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert isinstance(doc, Document)

    def test_one_section_per_page(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        page_texts = ["Page one text here.", "Page two text here.", "Page three."]
        stubs, refs = _make_tesseract_stubs(page_texts)

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert len(doc.sections) == 3

    def test_section_headings_contain_page_number(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["Some text on page one."])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert any(
            "Page 1" in " ".join(r.text for r in s.heading)
            for s in doc.sections if s.heading
        )

    def test_text_extracted_into_paragraphs(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["Hello world from tesseract OCR."])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        all_text = " ".join(
            r.text
            for s in doc.sections
            for b in s.blocks
            if isinstance(b, Paragraph)
            for r in b.runs
        )
        assert "Hello world" in all_text

    def test_source_format_set_to_pdf(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, _ = _make_tesseract_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert doc.metadata.source_format == "pdf"

    def test_page_count_in_metadata(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["p1", "p2", "p3"])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert doc.metadata.page_count == 3

    def test_bytes_input_uses_convert_from_bytes(self):
        stubs, refs = _make_tesseract_stubs(["Text from bytes."])

        with patch.dict(sys.modules, stubs):
            ocr_via_tesseract(b"fake pdf bytes")

        refs["pdf2image"].convert_from_bytes.assert_called_once()
        refs["pdf2image"].convert_from_path.assert_not_called()

    def test_path_input_uses_convert_from_path(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["Text from path."])

        with patch.dict(sys.modules, stubs):
            ocr_via_tesseract(src)

        refs["pdf2image"].convert_from_path.assert_called_once()
        refs["pdf2image"].convert_from_bytes.assert_not_called()

    def test_custom_dpi_forwarded(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["text"])
        opts = ParseOptions()
        opts.extra["ocr_dpi"] = 150

        with patch.dict(sys.modules, stubs):
            ocr_via_tesseract(src, opts)

        call_args = refs["pdf2image"].convert_from_path.call_args
        assert call_args[1].get("dpi") == 150 or 150 in call_args[0]

    def test_custom_lang_forwarded(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["text"])
        opts = ParseOptions()
        opts.extra["ocr_lang"] = "fra"

        with patch.dict(sys.modules, stubs):
            ocr_via_tesseract(src, opts)

        calls = refs["pytesseract"].image_to_string.call_args_list
        assert all(c[1].get("lang") == "fra" or "fra" in c[0] for c in calls)

    def test_raises_when_not_installed(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"x")

        with patch.dict(sys.modules, {"pytesseract": None, "pdf2image": None}):
            with pytest.raises(ParseError, match="not installed"):
                ocr_via_tesseract(src)

    def test_raises_when_tesseract_binary_missing(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"x")
        stubs, refs = _make_tesseract_stubs()
        refs["pytesseract"].get_tesseract_version.side_effect = EnvironmentError(
            "tesseract not found"
        )

        with patch.dict(sys.modules, stubs):
            with pytest.raises(ParseError, match="not found"):
                ocr_via_tesseract(src)

    def test_page_error_adds_warning_but_continues(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, refs = _make_tesseract_stubs(["good page", "bad page", "good page"])
        # Make the second page fail
        call_count = [0]
        def failing_ocr(img, lang="eng"):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 1:
                raise RuntimeError("OCR error on page 2")
            return "good text here"
        refs["pytesseract"].image_to_string.side_effect = failing_ocr

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert any("failed" in w.lower() or "tesseract" in w.lower()
                   for w in doc.warnings)
        # Should still have sections for pages that succeeded
        assert len(doc.sections) >= 2

    def test_empty_pages_produce_no_sections(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        # Pages return nothing but whitespace
        stubs, refs = _make_tesseract_stubs(["   \n   ", "\n\n\n"])

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert len(doc.sections) == 0

    def test_warnings_mention_tesseract(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        stubs, _ = _make_tesseract_stubs()

        with patch.dict(sys.modules, stubs):
            doc = ocr_via_tesseract(src)

        assert any("tesseract" in w.lower() or "ocr" in w.lower()
                   for w in doc.warnings)


# ── ocr_pdf (routing logic) ───────────────────────────────────────────────────

class TestOcrPdf:
    def test_uses_docling_by_default(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")

        with patch("distill.parsers._ocr.ocr_via_docling") as mock_docling, \
             patch("distill.parsers._ocr.ocr_via_tesseract") as mock_tess:
            mock_docling.return_value = Document()
            ocr_pdf(src)

        mock_docling.assert_called_once()
        mock_tess.assert_not_called()

    def test_falls_back_to_tesseract_when_docling_not_installed(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")

        with patch("distill.parsers._ocr.ocr_via_docling",
                   side_effect=ParseError("docling is not installed: ...")), \
             patch("distill.parsers._ocr.ocr_via_tesseract") as mock_tess:
            mock_tess.return_value = Document()
            ocr_pdf(src)

        mock_tess.assert_called_once()

    def test_force_docling_via_option(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        opts = ParseOptions()
        opts.extra["ocr_backend"] = "docling"

        with patch("distill.parsers._ocr.ocr_via_docling") as mock_docling, \
             patch("distill.parsers._ocr.ocr_via_tesseract") as mock_tess:
            mock_docling.return_value = Document()
            ocr_pdf(src, opts)

        mock_docling.assert_called_once()
        mock_tess.assert_not_called()

    def test_force_tesseract_via_option(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")
        opts = ParseOptions()
        opts.extra["ocr_backend"] = "tesseract"

        with patch("distill.parsers._ocr.ocr_via_docling") as mock_docling, \
             patch("distill.parsers._ocr.ocr_via_tesseract") as mock_tess:
            mock_tess.return_value = Document()
            ocr_pdf(src, opts)

        mock_tess.assert_called_once()
        mock_docling.assert_not_called()

    def test_raises_when_both_unavailable(self, tmp_path):
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")

        with patch("distill.parsers._ocr.ocr_via_docling",
                   side_effect=ParseError("docling is not installed: ...")), \
             patch("distill.parsers._ocr.ocr_via_tesseract",
                   side_effect=ParseError("pytesseract / pdf2image not installed: ...")):
            with pytest.raises(ParseError, match="no OCR backend"):
                ocr_pdf(src)

    def test_docling_real_failure_propagated(self, tmp_path):
        """If docling IS installed but fails on this file, propagate the error."""
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")

        with patch("distill.parsers._ocr.ocr_via_docling",
                   side_effect=ParseError("docling conversion failed: corrupt data")):
            with pytest.raises(ParseError, match="conversion failed"):
                ocr_pdf(src)

    def test_tesseract_real_failure_propagated(self, tmp_path):
        """If Tesseract IS installed but rasterisation fails, propagate."""
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"fake pdf")

        with patch("distill.parsers._ocr.ocr_via_docling",
                   side_effect=ParseError("docling is not installed: ...")), \
             patch("distill.parsers._ocr.ocr_via_tesseract",
                   side_effect=ParseError("pdf2image rasterisation failed: bad file")):
            with pytest.raises(ParseError, match="rasterisation failed"):
                ocr_pdf(src)


# ── PdfParser integration ─────────────────────────────────────────────────────

class TestPdfParserOcrIntegration:
    """
    Verify that PdfParser triggers OCR automatically when native extraction
    yields sparse content, and gracefully handles OCR being unavailable.
    """

    def _make_minimal_pdf(self, tmp_path: Path, name: str = "scan.pdf") -> Path:
        """Write a minimal (blank page) PDF to tmp_path."""
        p = tmp_path / name
        p.write_bytes(_make_pdf_bytes())
        return p

    def test_native_pdf_does_not_trigger_ocr(self, tmp_path):
        """A real PDF with extractable text must NOT call ocr_pdf."""
        from distill.parsers.pdf import PdfParser

        # Build a PDF with a genuine text layer
        try:
            from fpdf import FPDF
            pdf_obj = FPDF()
            pdf_obj.add_page()
            pdf_obj.set_font("Helvetica", size=12)
            pdf_obj.multi_cell(0, 10, "Word " * 20 + "sentence " * 5)
            content = pdf_obj.output(dest="S").encode("latin-1")
        except Exception:
            pytest.skip("fpdf2 not available for native PDF fixture")

        src = tmp_path / "native.pdf"
        src.write_bytes(content)

        with patch("distill.parsers._ocr.ocr_pdf") as mock_ocr:
            doc = PdfParser().parse(src)

        mock_ocr.assert_not_called()
        assert isinstance(doc, Document)

    def test_scanned_pdf_triggers_ocr(self, tmp_path):
        """A blank-page PDF with enable_ocr=True must call ocr_pdf."""
        from distill.parsers.pdf import PdfParser

        src = self._make_minimal_pdf(tmp_path)
        expected_doc = Document(
            sections=[Section(blocks=[Paragraph(runs=[TextRun(text="OCR text")])])]
        )
        opts = ParseOptions(extra={"enable_ocr": True})

        with patch("distill.parsers._ocr.ocr_pdf", return_value=expected_doc) as mock_ocr:
            doc = PdfParser().parse(src, opts)

        mock_ocr.assert_called_once()
        assert doc is expected_doc

    def test_ocr_unavailable_returns_sparse_native_with_warning(self, tmp_path):
        """If OCR fails, return the sparse native document with a warning."""
        from distill.parsers.pdf import PdfParser

        src = self._make_minimal_pdf(tmp_path)
        opts = ParseOptions(extra={"enable_ocr": True})

        with patch("distill.parsers._ocr.ocr_pdf",
                   side_effect=ParseError("no OCR backend is available")):
            doc = PdfParser().parse(src, opts)

        assert isinstance(doc, Document)
        assert any("ocr" in w.lower() or "not available" in w.lower()
                   for w in doc.warnings)

    def test_ocr_options_forwarded(self, tmp_path):
        """ParseOptions passed to PdfParser must reach ocr_pdf when enable_ocr=True."""
        from distill.parsers.pdf import PdfParser

        src = self._make_minimal_pdf(tmp_path)
        opts = ParseOptions()
        opts.extra["enable_ocr"]  = True
        opts.extra["ocr_backend"] = "tesseract"
        opts.extra["ocr_dpi"]     = 150

        captured_opts = []

        def capture(source, options=None):
            captured_opts.append(options)
            return Document()

        with patch("distill.parsers._ocr.ocr_pdf", side_effect=capture):
            PdfParser().parse(src, opts)

        assert captured_opts
        assert captured_opts[0].extra.get("ocr_backend") == "tesseract"
        assert captured_opts[0].extra.get("ocr_dpi") == 150
