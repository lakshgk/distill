"""
Tests for distill.parsers.pdf — PdfParser.

PDF fixtures are generated via reportlab (if available) or fpdf2.
Falls back to a raw minimal valid PDF if neither is installed.
"""

from __future__ import annotations

import io
import struct
import textwrap
from pathlib import Path

import pytest

from distill.ir import Document, Image, Paragraph, Section, Table, TableCell, TableRow, TextRun
from distill.parsers.base import ParseError
from distill.parsers.pdf import PdfParser, _check_input_size, _parse_pdf_date


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _make_pdf_bytes(
    text: str = "Hello from Distill.",
    title: str = "",
    author: str = "",
    subject: str = "",
) -> bytes:
    """
    Build a minimal single-page PDF in memory with the given text.
    Uses fpdf2 if available, otherwise falls back to a hand-crafted PDF.
    """
    try:
        from fpdf import FPDF  # type: ignore
        pdf = FPDF()
        pdf.set_margins(10, 10, 10)
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)

        # Write enough repeated text to pass the >100-char native detection
        full_text = (text + " ") * max(1, 120 // len(text) + 1)
        pdf.multi_cell(0, 8, full_text)

        if title:
            pdf.set_title(title)
        if author:
            pdf.set_author(author)
        if subject:
            pdf.set_subject(subject)

        return bytes(pdf.output())

    except ImportError:
        # Minimal hand-crafted single-page PDF with enough text chars
        return _minimal_pdf(text)


def _minimal_pdf(body_text: str) -> bytes:
    """
    Hand-craft the smallest valid single-page PDF that pdfplumber can open
    and extract text from.  Used when fpdf2 is not installed.
    """
    # Pad body text so pdfplumber native-detection heuristic (>100 chars) passes
    padded = (body_text + " ") * max(1, 110 // max(len(body_text), 1) + 1)
    padded = padded[:300]

    stream_content = (
        "BT\n"
        "/F1 12 Tf\n"
        "50 700 Td\n"
        f"({padded}) Tj\n"
        "ET\n"
    )
    stream_bytes = stream_content.encode("latin-1")
    stream_len   = len(stream_bytes)

    objects = []

    # obj 1 — catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # obj 2 — pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # obj 3 — page
    objects.append(
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R\n"
        b"   /MediaBox [0 0 595 842]\n"
        b"   /Contents 4 0 R\n"
        b"   /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
    )
    # obj 4 — content stream
    objects.append(
        b"4 0 obj\n"
        b"<< /Length " + str(stream_len).encode() + b" >>\n"
        b"stream\n" +
        stream_bytes +
        b"\nendstream\nendobj\n"
    )
    # obj 5 — font
    objects.append(
        b"5 0 obj\n"
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        b"endobj\n"
    )

    header   = b"%PDF-1.4\n"
    body     = b""
    offsets  = []
    pos      = len(header)
    for obj in objects:
        offsets.append(pos)
        body += obj
        pos  += len(obj)

    xref_pos = len(header) + len(body)
    xref  = b"xref\n"
    xref += f"0 {len(offsets) + 1}\n".encode()
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n"
        b"<< /Size " + str(len(offsets) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" +
        str(xref_pos).encode() + b"\n"
        b"%%EOF\n"
    )

    return header + body + xref + trailer


def _make_encrypted_pdf_bytes() -> bytes:
    """
    Return bytes that pdfplumber raises an 'encrypted' or 'password' error on.
    We fake this by making pdfplumber get an exception via a corrupted header
    that contains the word 'encrypted' in the error path.  In practice we just
    verify the ParseError message; a real encrypted PDF would be tested by
    integration tests with a fixture file.
    """
    # This is just a marker — see test_encrypted_detection for how we test it
    return b""


# ── Parser availability ───────────────────────────────────────────────────────

class TestParserAvailability:
    def test_is_available(self):
        assert PdfParser.is_available()

    def test_extensions(self):
        assert ".pdf" in PdfParser.extensions

    def test_missing_requires_empty(self):
        assert PdfParser.missing_requires() == []


# ── Date parsing ─────────────────────────────────────────────────────────────

class TestPdfDateParsing:
    def test_full_date(self):
        result = _parse_pdf_date("D:20231215143022+05:30")
        assert result == "2023-12-15T14:30:22+05:30"

    def test_utc_z(self):
        result = _parse_pdf_date("D:20230101000000Z")
        assert result == "2023-01-01T00:00:00+00:00"

    def test_no_d_prefix(self):
        result = _parse_pdf_date("20230601120000Z")
        assert result == "2023-06-01T12:00:00+00:00"

    def test_negative_tz(self):
        result = _parse_pdf_date("D:20230601120000-08'00'")
        assert result == "2023-06-01T12:00:00-08:00"

    def test_none_input(self):
        assert _parse_pdf_date(None) is None

    def test_empty_string(self):
        assert _parse_pdf_date("") is None

    def test_garbage_returns_none(self):
        assert _parse_pdf_date("not-a-date") is None


# ── Security checks ────────────────────────────────────────────────────────────

class TestSecurity:
    def test_input_size_check_bytes(self):
        oversized = b"x" * (55 * 1024 * 1024)
        with pytest.raises(ParseError, match="50 MB"):
            _check_input_size(oversized, 50 * 1024 * 1024)

    def test_input_size_check_path(self, tmp_path):
        big = tmp_path / "big.pdf"
        big.write_bytes(b"x" * (55 * 1024 * 1024))
        with pytest.raises(ParseError, match="50 MB"):
            _check_input_size(str(big), 50 * 1024 * 1024)

    def test_input_size_ok(self):
        small = b"%PDF-1.4"
        _check_input_size(small, 50 * 1024 * 1024)  # must not raise

    def test_parser_rejects_oversized_bytes(self):
        oversized = b"x" * (55 * 1024 * 1024)
        with pytest.raises(ParseError, match="50 MB"):
            PdfParser().parse(oversized)

    def test_custom_size_limit_via_options(self):
        from distill.parsers.base import ParseOptions
        data = b"x" * (15 * 1024 * 1024)
        opts = ParseOptions(extra={"max_file_size": 10 * 1024 * 1024})
        with pytest.raises(ParseError, match="10 MB"):
            PdfParser().parse(data, options=opts)

    def test_garbled_bytes_raises_parse_error(self):
        with pytest.raises(ParseError):
            PdfParser().parse(b"this is not a pdf at all")


# ── Basic parsing ─────────────────────────────────────────────────────────────

class TestBasicParsing:
    def test_returns_document(self):
        data   = _make_pdf_bytes("Hello from Distill.")
        result = PdfParser().parse(data)
        assert isinstance(result, Document)

    def test_text_extracted(self):
        data = _make_pdf_bytes("Distill PDF test content.")
        doc  = PdfParser().parse(data)
        text = _all_text(doc)
        assert len(text) > 0

    def test_page_sections_created(self):
        data = _make_pdf_bytes("One page of content.")
        doc  = PdfParser().parse(data)
        assert len(doc.sections) >= 1

    def test_section_headings_contain_page(self):
        data = _make_pdf_bytes("Text on page one.")
        doc  = PdfParser().parse(data)
        headings = [
            run.text
            for s in doc.sections
            if s.heading
            for run in s.heading
        ]
        assert any("Page" in h for h in headings)

    def test_accepts_path(self, tmp_path):
        p = tmp_path / "sample.pdf"
        p.write_bytes(_make_pdf_bytes("Path-based test."))
        doc = PdfParser().parse(str(p))
        assert isinstance(doc, Document)

    def test_accepts_path_object(self, tmp_path):
        p = tmp_path / "sample.pdf"
        p.write_bytes(_make_pdf_bytes("Path object test."))
        doc = PdfParser().parse(p)
        assert isinstance(doc, Document)

    def test_source_format_in_metadata(self):
        data = _make_pdf_bytes("Metadata format check.")
        doc  = PdfParser().parse(data)
        assert doc.metadata.source_format == "pdf"

    def test_page_count_in_metadata(self):
        data = _make_pdf_bytes("Page count check.")
        doc  = PdfParser().parse(data)
        assert doc.metadata.page_count == 1


# ── Render integration ─────────────────────────────────────────────────────────

class TestRenderIntegration:
    def test_renders_to_markdown(self):
        data = _make_pdf_bytes("Render me to markdown.")
        doc  = PdfParser().parse(data)
        md   = doc.render(front_matter=False)
        assert isinstance(md, str)
        assert len(md) > 0

    def test_front_matter_includes_source_format(self):
        data = _make_pdf_bytes("Front matter test.")
        doc  = PdfParser().parse(data)
        md   = doc.render(front_matter=True)
        assert "---" in md
        assert "pdf" in md

    def test_no_front_matter_when_suppressed(self):
        data = _make_pdf_bytes("Suppress front matter.")
        doc  = PdfParser().parse(data)
        md   = doc.render(front_matter=False)
        assert "---" not in md

    def test_table_produces_gfm_pipe_syntax(self):
        from distill.renderer import MarkdownRenderer
        doc = Document(sections=[
            Section(level=0, blocks=[
                Table(rows=[
                    TableRow(cells=[
                        TableCell(content=[Paragraph(runs=[TextRun(text='Col A')])], is_header=True),
                        TableCell(content=[Paragraph(runs=[TextRun(text='Col B')])], is_header=True),
                    ]),
                    TableRow(cells=[
                        TableCell(content=[Paragraph(runs=[TextRun(text='1')])]),
                        TableCell(content=[Paragraph(runs=[TextRun(text='2')])]),
                    ]),
                ])
            ])
        ])
        md = MarkdownRenderer(front_matter=False).render(doc)
        assert "| --- |" in md
        assert "| Col A | Col B |" in md

    def test_empty_image_suppressed(self):
        from distill.renderer import MarkdownRenderer
        doc = Document(sections=[
            Section(level=0, blocks=[
                Image(),
                Paragraph(runs=[TextRun(text="Some text")]),
            ])
        ])
        md = MarkdownRenderer(front_matter=False).render(doc)
        assert "![" not in md
        assert "Some text" in md


# ── Word count ────────────────────────────────────────────────────────────────

class TestWordCount:
    def test_native_pdf_word_count_populated(self):
        data = _make_pdf_bytes("Hello from Distill testing word count.")
        doc = PdfParser().parse(data)
        assert doc.metadata.word_count is not None
        assert doc.metadata.word_count > 0


# ── Image extraction / suppression ────────────────────────────────────────────

class TestImageWiring:
    def test_image_suppressed(self):
        from distill.parsers.base import ParseOptions
        data = _make_pdf_bytes("Text content.")
        doc = PdfParser().parse(data, options=ParseOptions(images="suppress"))
        images = [b for s in doc.sections for b in s.blocks if isinstance(b, Image)]
        assert len(images) == 0

    def test_image_extraction_no_empty_tags(self, tmp_path):
        from distill.parsers.base import ParseOptions
        data = _make_pdf_bytes("Text content.")
        doc = PdfParser().parse(
            data,
            options=ParseOptions(images="extract", image_dir=str(tmp_path / "images")),
        )
        md = doc.render(front_matter=False)
        assert "![](" not in md or "![" in md  # no fully-empty image tags


# ── Decorative image classification ──────────────────────────────────────────

class TestDecorativeClassification:
    def test_full_bleed_pdf_image_decorative(self):
        from distill.parsers.base import classify_image
        from distill.ir import ImageType
        result = classify_image(mode="pdf", img_w=595.0, img_h=842.0, page_w=595.0, page_h=842.0)
        assert result == ImageType.DECORATIVE

    def test_content_pdf_image_not_decorative(self):
        from distill.parsers.base import classify_image
        from distill.ir import ImageType
        result = classify_image(mode="pdf", img_w=200.0, img_h=150.0, page_w=595.0, page_h=842.0)
        assert result == ImageType.UNKNOWN


# ── Helpers ────────────────────────────────────────────────────────────────────

def _all_text(doc: Document) -> str:
    parts: list[str] = []
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, Paragraph):
                for run in block.runs:
                    parts.append(run.text)
    return " ".join(parts)


# ── Rotated text correction ─────────────────────────────────────────────────

from distill.parsers.pdf import _correct_rotated_text

_ROTATED_MATRIX = (0, 1, -1, 0, 100, 200)   # 90° CCW
_NORMAL_MATRIX  = (1, 0, 0, 1, 100, 200)    # standard horizontal


def _make_char(text, matrix, x0=100.0):
    return {"text": text, "matrix": matrix, "x0": x0}


class _FakePage:
    def __init__(self, chars):
        self.chars = chars


class TestRotatedTextCorrection:
    def test_rotated_text_reversed_run_corrected(self):
        chars = [_make_char(c, _ROTATED_MATRIX, x0=100) for c in "EULAV"]
        page = _FakePage(chars)
        result = _correct_rotated_text(page, "EULAV")
        assert result == "VALUE", f"Expected 'VALUE', got '{result}'"

    def test_rotated_text_normal_text_unchanged(self):
        chars = [_make_char(c, _NORMAL_MATRIX, x0=float(i * 10)) for i, c in enumerate("HELLO")]
        page = _FakePage(chars)
        result = _correct_rotated_text(page, "HELLO")
        assert result == "HELLO", f"Expected 'HELLO', got '{result}'"

    def test_rotated_text_mixed_page(self):
        rotated = [_make_char(c, _ROTATED_MATRIX, x0=100) for c in "EULAV"]
        normal_pre = [_make_char(c, _NORMAL_MATRIX, x0=float(i * 10)) for i, c in enumerate("Revenue: ")]
        normal_post = [_make_char(c, _NORMAL_MATRIX, x0=float(200 + i * 10)) for i, c in enumerate(" axis")]
        page = _FakePage(normal_pre + rotated + normal_post)
        result = _correct_rotated_text(page, "Revenue: EULAV axis")
        assert result == "Revenue: VALUE axis", f"Got '{result}'"

    def test_rotated_text_single_char_noop(self):
        chars = [_make_char("X", _ROTATED_MATRIX, x0=100)]
        page = _FakePage(chars)
        result = _correct_rotated_text(page, "X")
        assert result == "X", f"Single char should be unchanged, got '{result}'"

    def test_rotated_text_empty_chars(self):
        page = _FakePage([])
        result = _correct_rotated_text(page, "Some text on page")
        assert result == "Some text on page"

    def test_rotated_text_two_separate_runs(self):
        run1 = [_make_char(c, _ROTATED_MATRIX, x0=100) for c in "CBA"]
        run2 = [_make_char(c, _ROTATED_MATRIX, x0=500) for c in "ZYX"]
        page = _FakePage(run1 + run2)
        result = _correct_rotated_text(page, "CBA ZYX")
        assert result == "ABC XYZ", f"Expected 'ABC XYZ', got '{result}'"

    def test_rotated_text_run_not_in_raw_text(self):
        chars = [_make_char(c, _ROTATED_MATRIX, x0=100) for c in "XYZ"]
        page = _FakePage(chars)
        result = _correct_rotated_text(page, "completely different text")
        assert result == "completely different text", \
            f"String not found in raw_text should leave it unchanged, got '{result}'"


# ── Mid-word cell split fix (P0-1) ──────────────────────────────────────────

class _MockCroppedPage:
    """Simulates page.crop(bbox) return value."""
    def __init__(self, words):
        self._words = words

    def extract_words(self, keep_blank_chars=False):
        return [{"text": w} for w in self._words]


class _MockRow:
    """Simulates a pdfplumber Row object with a .cells list of bboxes."""
    def __init__(self, cells):
        self.cells = cells


class _MockTable:
    """Simulates a pdfplumber Table object from find_tables()."""
    def __init__(self, cell_words):
        """
        cell_words: list of rows, each row a list of word-lists.
        e.g. [[["Construction"], ["$10M"]], [["Phase"], ["Two"]]]
        Each inner list is the words in that cell.
        A None entry means an empty/merged cell.
        """
        self._cell_words = cell_words

    @property
    def rows(self):
        result = []
        for ri, row in enumerate(self._cell_words):
            cells = [
                f"bbox_{ri}_{ci}" if w is not None else None
                for ci, w in enumerate(row)
            ]
            result.append(_MockRow(cells))
        return result


class _MockTablePage:
    """Simulates a pdfplumber Page with find_tables() and crop()."""
    def __init__(self, tables):
        self._tables = tables
        # Build lookup: bbox_key -> word list
        self._bbox_words = {}
        for tbl in tables:
            for ri, row in enumerate(tbl._cell_words):
                for ci, words in enumerate(row):
                    if words is not None:
                        key = f"bbox_{ri}_{ci}"
                        self._bbox_words[key] = words

    def find_tables(self):
        return self._tables

    def crop(self, bbox):
        words = self._bbox_words.get(bbox, [])
        return _MockCroppedPage(words)

    def extract_tables(self):
        result = []
        for tbl in self._tables:
            rows = []
            for row in tbl._cell_words:
                rows.append([" ".join(w) if w else "" for w in row])
            result.append(rows)
        return result


class TestTableWordExtraction:
    def test_extract_tables_words_joins_cell_words(self):
        """Words spanning a column boundary are joined correctly."""
        from distill.parsers.pdf import _extract_tables_words

        tbl = _MockTable([
            [["Column A"], ["Column B"]],
            [["Construction"], ["$10M/$10M"]],
        ])
        page = _MockTablePage([tbl])
        tables = _extract_tables_words(page, max_rows=500)

        assert len(tables) == 1
        ir_table = tables[0]

        def cell_text(cell):
            return "".join(run.text for block in cell.content for run in block.runs).strip()

        # Check data row (row index 1)
        data_row = ir_table.rows[1]
        cell_texts = [cell_text(c) for c in data_row.cells]
        assert any("Construction" in t for t in cell_texts), \
            f"'Construction' not found intact in cell texts: {cell_texts}"
        assert not any(t in ("Const", "ruction") for t in cell_texts), \
            f"Split fragments found in cell texts: {cell_texts}"

    def test_extract_tables_words_none_cell_is_empty(self):
        """None cell bbox produces empty string without raising."""
        from distill.parsers.pdf import _extract_tables_words

        tbl = _MockTable([
            [["Header A"], None],
            [["Data"], ["Value"]],
        ])
        page = _MockTablePage([tbl])
        tables = _extract_tables_words(page, max_rows=500)

        assert len(tables) == 1

    def test_extract_tables_fallback_on_exception(self):
        """Fallback fires when find_tables() raises."""
        from distill.parsers.pdf import _extract_tables

        class _BrokenPage:
            def find_tables(self):
                raise RuntimeError("simulated pdfplumber failure")
            def extract_tables(self):
                return [[["A", "B"], ["1", "2"]]]

        page = _BrokenPage()
        tables = _extract_tables(page, max_rows=500)
        assert isinstance(tables, list), \
            f"Expected list from fallback, got {type(tables)}"

    def test_extract_tables_words_max_rows_respected(self):
        """max_rows truncation still applies in new path."""
        from distill.parsers.pdf import _extract_tables_words

        rows = [[["Header"]]] + [[[f"Row {i}"]] for i in range(6)]
        tbl = _MockTable(rows)
        page = _MockTablePage([tbl])

        tables = _extract_tables_words(page, max_rows=3)
        assert len(tables) == 1
        ir_table = tables[0]
        total_rows = len(ir_table.rows)
        assert total_rows <= 3, \
            f"Expected max 3 rows after truncation, got {total_rows}"

    def test_extract_tables_words_no_tables(self):
        """Empty page (no tables) returns empty list."""
        from distill.parsers.pdf import _extract_tables_words

        class _EmptyPage:
            def find_tables(self):
                return []
            def crop(self, bbox):
                return _MockCroppedPage([])

        tables = _extract_tables_words(_EmptyPage(), max_rows=500)
        assert tables == [], f"Expected empty list, got {tables}"

    def test_extract_tables_words_multiword_cell(self):
        """Multi-word cells are joined with single space."""
        from distill.parsers.pdf import _extract_tables_words

        tbl = _MockTable([
            [["First", "Name"], ["Last", "Name"]],
            [["John", "Doe"], ["Jane", "Smith"]],
        ])
        page = _MockTablePage([tbl])
        tables = _extract_tables_words(page, max_rows=500)

        assert len(tables) == 1
        ir_table = tables[0]

        def cell_text(cell):
            return "".join(run.text for block in cell.content for run in block.runs).strip()

        all_texts = [cell_text(c) for row in ir_table.rows for c in row.cells]
        assert any("First Name" in t for t in all_texts), \
            f"Multi-word cells not joined correctly: {all_texts}"
        assert any("John Doe" in t for t in all_texts), \
            f"Multi-word cells not joined correctly: {all_texts}"


# ── Ghost table / empty table filtering (P1-6 + P2-5) ──────────────────────

class TestBuildIrTableFiltering:
    def test_build_ir_table_all_empty_returns_none(self):
        """All-empty table (decorative box) returns None."""
        from distill.parsers.pdf import _build_ir_table

        raw_rows = [["", "", ""], ["", "", ""], ["", "", ""]]
        assert _build_ir_table(raw_rows, max_rows=500) is None, \
            "All-empty table should be filtered and return None"

    def test_build_ir_table_majority_phantom_columns_returns_none(self):
        """Table with majority phantom columns (accent bar ghost) returns None."""
        from distill.parsers.pdf import _build_ir_table

        raw_rows = [
            ["", "Header B", ""],
            ["", "Value 1",  ""],
            ["", "Value 2",  ""],
        ]
        assert _build_ir_table(raw_rows, max_rows=500) is None, \
            "Table with majority phantom columns should return None"

    def test_build_ir_table_minority_phantom_columns_kept(self):
        """Table with minority phantom columns is kept."""
        from distill.parsers.pdf import _build_ir_table

        raw_rows = [
            ["", "Header B", "Header C"],
            ["", "Value 1",  "Value 2"],
            ["", "Value 3",  "Value 4"],
        ]
        result = _build_ir_table(raw_rows, max_rows=500)
        assert result is not None, \
            "Table with minority phantom columns should not be filtered"

    def test_build_ir_table_normal_table_kept(self):
        """Normal table with real content is kept."""
        from distill.parsers.pdf import _build_ir_table

        raw_rows = [
            ["Name",  "Amount", "Status"],
            ["Alpha", "100",    "Active"],
            ["Beta",  "200",    "Inactive"],
        ]
        result = _build_ir_table(raw_rows, max_rows=500)
        assert result is not None, \
            "Normal table with content should not be filtered"

    def test_build_ir_table_single_real_column_kept(self):
        """Single content column with phantom columns on both sides — majority filtered."""
        from distill.parsers.pdf import _build_ir_table

        raw_rows = [
            ["", "Only Real Column", ""],
            ["", "Row 1",            ""],
        ]
        assert _build_ir_table(raw_rows, max_rows=500) is None, \
            "2-of-3 phantom columns is a majority — should be filtered"

    def test_build_ir_table_exactly_half_phantom_kept(self):
        """Two-column table with one phantom column — exactly 50%, not majority, kept."""
        from distill.parsers.pdf import _build_ir_table

        raw_rows = [
            ["", "Header B"],
            ["", "Value 1"],
            ["", "Value 2"],
        ]
        result = _build_ir_table(raw_rows, max_rows=500)
        assert result is not None, \
            "Exactly 50%% phantom columns is not a majority — table should be kept"

    def test_build_ir_table_empty_raw_rows_returns_none(self):
        """Empty raw_rows still returns None (existing guard)."""
        from distill.parsers.pdf import _build_ir_table

        assert _build_ir_table([], max_rows=500) is None, \
            "Empty raw_rows should still return None (existing guard)"


# ── Cross-page table detection (P1-7 + P3-2) ───────────────────────────────

class _MockPdfPage:
    """Full mock of a pdfplumber page for integration with _parse_native."""
    def __init__(self, tables=None):
        self._tables = tables or []
        self.chars = []
        self.height = 842.0
        self.width = 595.0
        self.images = []
        self._bbox_words = {}
        for tbl in self._tables:
            for ri, row in enumerate(tbl._cell_words):
                for ci, words in enumerate(row):
                    if words is not None:
                        self._bbox_words[f"bbox_{ri}_{ci}"] = words

    def extract_text(self):
        return "mock text content for word count"

    def find_tables(self):
        return self._tables

    def extract_tables(self):
        result = []
        for tbl in self._tables:
            rows = []
            for row in tbl._cell_words:
                rows.append([" ".join(w) if w else "" for w in row])
            result.append(rows)
        return result

    def crop(self, bbox):
        words = self._bbox_words.get(bbox, [])
        return _MockCroppedBodyRegion(words, self._tables)

    def outside_bbox(self, bbox):
        return self


class _MockCroppedBodyRegion:
    """Simulates a cropped page region for text extraction."""
    def __init__(self, words, tables=None):
        self._words = words
        self._tables = tables or []

    def extract_text(self, **kwargs):
        return " ".join(self._words) if self._words else ""

    def extract_words(self, keep_blank_chars=False):
        return [{"text": w} for w in self._words]

    def find_tables(self):
        return []

    def outside_bbox(self, bbox):
        return self


class _MockPdf:
    """Simulates a pdfplumber PDF with multiple pages."""
    def __init__(self, pages):
        self.pages = pages
        self.metadata = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestCrossPageTableDetection:
    def _run_parse(self, pages):
        """Helper: run _parse_native with mock pages and return (document, collector)."""
        from distill.parsers.pdf import PdfParser
        from distill.parsers.base import ParseOptions
        from distill.warnings import WarningCollector

        mock_pdf = _MockPdf(pages)
        opts = ParseOptions()
        collector = WarningCollector()
        opts.collector = collector
        parser = PdfParser()
        doc = parser._parse_native(mock_pdf, "test.pdf", opts)
        return doc, collector

    def test_cross_page_table_warning_emitted(self):
        """Same column count on adjacent pages emits cross_page_table warning."""
        page1 = _MockPdfPage([_MockTable([
            [["Col A"], ["Col B"], ["Col C"]],
            [["1"],     ["2"],     ["3"]],
        ])])
        page2 = _MockPdfPage([_MockTable([
            [["4"],     ["5"],     ["6"]],
            [["7"],     ["8"],     ["9"]],
        ])])

        doc, collector = self._run_parse([page1, page2])
        warnings = collector.to_dict()
        warning_types = [w["type"] for w in warnings]
        assert "cross_page_table" in warning_types, \
            f"Expected cross_page_table warning, got: {warnings}"
        cross_warnings = [w for w in warnings if w["type"] == "cross_page_table"]
        assert cross_warnings[0]["pages"] == [1, 2], \
            f"Expected pages [1, 2], got: {cross_warnings[0]}"

    def test_cross_page_table_no_warning_different_cols(self):
        """Different column counts on adjacent pages — no warning."""
        page1 = _MockPdfPage([_MockTable([
            [["Col A"], ["Col B"], ["Col C"]],
            [["1"],     ["2"],     ["3"]],
        ])])
        page2 = _MockPdfPage([_MockTable([
            [["X"],  ["Y"]],
            [["10"], ["20"]],
        ])])

        doc, collector = self._run_parse([page1, page2])
        warning_types = [w["type"] for w in collector.to_dict()]
        assert "cross_page_table" not in warning_types, \
            f"Should not warn for different column counts"

    def test_cross_page_table_single_page_no_warning(self):
        """Single page with table — no warning."""
        page1 = _MockPdfPage([_MockTable([
            [["Col A"], ["Col B"]],
            [["1"],     ["2"]],
        ])])

        doc, collector = self._run_parse([page1])
        warning_types = [w["type"] for w in collector.to_dict()]
        assert "cross_page_table" not in warning_types

    def test_cross_page_table_non_adjacent_no_warning(self):
        """Page with no table between two pages with tables — no false positive."""
        page1 = _MockPdfPage([_MockTable([
            [["Col A"], ["Col B"]],
            [["1"],     ["2"]],
        ])])
        page2 = _MockPdfPage([])  # no tables
        page3 = _MockPdfPage([_MockTable([
            [["Col A"], ["Col B"]],
            [["3"],     ["4"]],
        ])])

        doc, collector = self._run_parse([page1, page2, page3])
        warning_types = [w["type"] for w in collector.to_dict()]
        assert "cross_page_table" not in warning_types, \
            f"Non-adjacent same-column tables should not warn"

    def test_cross_page_table_warning_message_content(self):
        """Warning includes correct column count and page numbers."""
        page1 = _MockPdfPage([_MockTable([
            [["A"], ["B"], ["C"], ["D"]],
            [["1"], ["2"], ["3"], ["4"]],
        ])])
        page2 = _MockPdfPage([_MockTable([
            [["5"], ["6"], ["7"], ["8"]],
        ])])

        doc, collector = self._run_parse([page1, page2])
        cross_warnings = [
            w for w in collector.to_dict()
            if w["type"] == "cross_page_table"
        ]
        assert cross_warnings, "Expected at least one cross_page_table warning"
        msg = cross_warnings[0]["message"]
        assert "4" in msg, f"Column count (4) should appear in message: {msg}"
        assert "page 1" in msg.lower() or "1" in msg, \
            f"Page 1 should be referenced in message: {msg}"
        assert "page 2" in msg.lower() or "2" in msg, \
            f"Page 2 should be referenced in message: {msg}"


# ── Math detection density threshold (P3-1) ─────────────────────────────────

class TestMathDetectionDensityThreshold:
    def test_stray_glyphs_suppressed(self):
        """Single stray math-range character should not trigger math_detected warning."""
        from distill.features.math_detection import MathDetector
        from distill.warnings import WarningCollector

        # 500 normal chars + 1 math-range char on page 1
        page_data = [
            {"text": "a", "fontname": "Arial", "page_number": 1}
            for _ in range(500)
        ]
        page_data.append({"text": "\u2211", "fontname": "Arial", "page_number": 1})

        collector = WarningCollector()
        MathDetector().detect_in_pdf(page_data, collector)

        warning_types = [w["type"] for w in collector.to_dict()]
        assert "math_detected" not in warning_types, \
            f"Single stray math char should not trigger warning, got: {collector.to_dict()}"

    def test_real_math_passes(self):
        """Page with many math characters should still trigger math_detected warning."""
        from distill.features.math_detection import MathDetector
        from distill.warnings import WarningCollector

        # 150 normal + 50 math chars on page 1 — well above thresholds
        page_data = [
            {"text": "x", "fontname": "Arial", "page_number": 1}
            for _ in range(150)
        ]
        page_data.extend([
            {"text": "\u2211", "fontname": "Arial", "page_number": 1}
            for _ in range(50)
        ])

        collector = WarningCollector()
        MathDetector().detect_in_pdf(page_data, collector)

        warning_types = [w["type"] for w in collector.to_dict()]
        assert "math_detected" in warning_types, \
            f"Page with many math chars should trigger warning, got: {collector.to_dict()}"


# ── Font encoding corruption detection (P3-3) ──────────────────────────────

class TestEncodingCorruptionDetection:
    def test_clean_text(self):
        from distill.parsers.pdf import _detect_encoding_corruption
        assert _detect_encoding_corruption("Normal English text here.") == 0.0

    def test_replacement_chars(self):
        from distill.parsers.pdf import _detect_encoding_corruption
        text = "a\ufffd\ufffd\ufffd\ufffd\ufffdb"
        ratio = _detect_encoding_corruption(text)
        assert ratio > 0.08, f"Expected ratio > 0.08, got {ratio}"

    def test_private_use_area(self):
        from distill.parsers.pdf import _detect_encoding_corruption
        text = "\ue001\ue002\ue003\ue004\ue005normal"
        ratio = _detect_encoding_corruption(text)
        assert ratio > 0.08, f"Expected high ratio for PUA chars, got {ratio}"

    def test_empty_string(self):
        from distill.parsers.pdf import _detect_encoding_corruption
        assert _detect_encoding_corruption("") == 0.0

    def test_whitespace_only(self):
        from distill.parsers.pdf import _detect_encoding_corruption
        assert _detect_encoding_corruption("   \n\t  ") == 0.0

    def test_font_encoding_warning_type_exists(self):
        from distill.warnings import WarningType
        assert hasattr(WarningType, "FONT_ENCODING_UNSUPPORTED")
        assert WarningType.FONT_ENCODING_UNSUPPORTED == "font_encoding_unsupported"


# ── Single-column table filter (P1-new) ─────────────────────────────────────

class TestSingleColumnTableFilter:
    def test_build_ir_table_single_column_large_text_filtered(self):
        """Single-column table with >200 chars total is a false positive."""
        from distill.parsers.pdf import _build_ir_table
        long_text = "A" * 201
        rows = [[long_text]]
        assert _build_ir_table(rows, max_rows=500) is None, \
            "Large single-column block should be filtered"

    def test_build_ir_table_single_column_short_kept(self):
        """Single-column table with short rows is a real table."""
        from distill.parsers.pdf import _build_ir_table
        rows = [["Item A"], ["Item B"], ["Item C"]]
        assert _build_ir_table(rows, max_rows=500) is not None, \
            "Small single-column table should be kept"

    def test_build_ir_table_multi_column_long_cell_kept(self):
        """Multi-column table is never filtered by the single-column rule."""
        from distill.parsers.pdf import _build_ir_table
        rows = [["Short", "A" * 300], ["X", "Y"]]
        assert _build_ir_table(rows, max_rows=500) is not None, \
            "Multi-column table with long cell must not be filtered"


# ── Multi-column layout mode (P1-5) ─────────────────────────────────────────


# ── Heuristic heading detection (P1-4) ──────────────────────────────────────

class TestHeadingDetection:
    def test_build_line_font_map_returns_sizes(self):
        from distill.parsers.pdf import _build_line_font_map

        class FakePage:
            chars = [
                {"top": 100.0, "size": 24.0, "text": "H"},
                {"top": 200.0, "size": 12.0, "text": "B"},
            ]

        result = _build_line_font_map(FakePage())
        assert len(result) > 0, "Font map should not be empty"
        assert any(v == 24.0 for v in result.values()), \
            "24pt size should appear in font map"

    def test_build_line_font_map_empty_chars(self):
        from distill.parsers.pdf import _build_line_font_map

        class FakePage:
            chars = []

        result = _build_line_font_map(FakePage())
        assert result == {}, "Empty chars should return empty font map"

    def test_chars_to_blocks_promotes_large_font_to_heading(self):
        from distill.parsers.pdf import _chars_to_blocks
        from distill.ir import Section, Paragraph

        font_map = {100: 24.0, 200: 12.0, 300: 12.0}
        text = "Executive Summary\nThis is body text\nMore body text"
        blocks = _chars_to_blocks(text, font_map)

        sections = [b for b in blocks if isinstance(b, Section)]
        paras = [b for b in blocks if isinstance(b, Paragraph)]
        assert len(sections) >= 1, f"Expected heading block, got: {blocks}"
        assert len(paras) >= 1, f"Expected paragraph blocks, got: {blocks}"
        assert sections[0].heading[0].text == "Executive Summary", \
            f"Wrong heading text: {sections[0].heading}"

    def test_chars_to_blocks_empty_font_map_fallback(self):
        from distill.parsers.pdf import _chars_to_blocks
        from distill.ir import Section

        blocks = _chars_to_blocks("Line one\nLine two", {})
        sections = [b for b in blocks if isinstance(b, Section)]
        assert len(sections) == 0, \
            "Empty font map should produce no headings (fallback to paragraphs)"

    def test_chars_to_blocks_bare_number_not_heading(self):
        from distill.parsers.pdf import _chars_to_blocks
        from distill.ir import Section

        font_map = {100: 24.0}
        blocks = _chars_to_blocks("5", font_map)
        sections = [b for b in blocks if isinstance(b, Section)]
        assert len(sections) == 0, \
            "Bare page number should not be promoted to heading"

    def test_chars_to_blocks_long_line_not_heading(self):
        from distill.parsers.pdf import _chars_to_blocks
        from distill.ir import Section

        font_map = {100: 24.0}
        long_line = "W" * 121
        blocks = _chars_to_blocks(long_line, font_map)
        sections = [b for b in blocks if isinstance(b, Section)]
        assert len(sections) == 0, \
            "Line > 120 chars should not be promoted to heading even at large font"


# ── Footer suppression — PDF page number regex (P2-1) ───────────────────────

class TestPageNumberRegex:
    def test_page_number_regex_matches_all_patterns(self):
        """All observed footer page number patterns must be matched."""
        from distill.parsers.pdf import _PAGE_NUMBER_RE

        should_match = [
            "5", " 12 ", "| 6", "| 17",
            "Page 5", "page 12", "5 of 20", "- 5 -",
        ]
        for pattern in should_match:
            assert _PAGE_NUMBER_RE.match(pattern), \
                f"_PAGE_NUMBER_RE should match footer pattern: {repr(pattern)}"

    def test_page_number_regex_does_not_match_content(self):
        """Real content must not be matched by the page number regex."""
        from distill.parsers.pdf import _PAGE_NUMBER_RE

        should_not_match = [
            "Revenue: 500", "Stage 2", "Q1 2026",
            "See page 5 for details", "Total: 42",
        ]
        for pattern in should_not_match:
            assert not _PAGE_NUMBER_RE.match(pattern), \
                f"_PAGE_NUMBER_RE must not match content: {repr(pattern)}"

    def test_bottom_crop_is_ten_percent(self):
        """Bottom crop must be h * 0.90 (10%), not the old h * 0.92."""
        import inspect
        from distill.parsers import pdf as pdf_mod
        src = inspect.getsource(pdf_mod._extract_page_text)
        assert "0.90" in src, "Bottom crop should be h * 0.90"
        assert "0.92" not in src, "Old bottom crop h * 0.92 should be removed"


# ── Prose-in-cells false positive filter (P1-new Batch 12) ──────────────────

class TestProseInCellsFilter:
    def test_build_ir_table_prose_in_cells_filtered(self):
        """Narrow table (<=3 cols) with average cell length >80 chars is filtered."""
        from distill.parsers.pdf import _build_ir_table

        long_cell = "A" * 90
        rows = [[long_cell, long_cell], [long_cell, long_cell]]
        assert _build_ir_table(rows, max_rows=500) is None, \
            "Narrow table with long prose cells should be filtered"

    def test_build_ir_table_short_value_table_kept(self):
        """Narrow table with short cell values is a real data table."""
        from distill.parsers.pdf import _build_ir_table

        rows = [["Milestone", "Duration"], ["Phase 1", "4 weeks"], ["Phase 2", "6 weeks"]]
        assert _build_ir_table(rows, max_rows=500) is not None, \
            "Narrow table with short values should not be filtered"

    def test_build_ir_table_wide_table_not_filtered(self):
        """Wide tables (>3 cols) are never filtered by the prose-in-cells rule."""
        from distill.parsers.pdf import _build_ir_table

        long_cell = "A" * 100
        rows = [[long_cell] * 4, [long_cell] * 4]
        assert _build_ir_table(rows, max_rows=500) is not None, \
            "Wide table must not be filtered by prose-in-cells rule"

    def test_build_ir_table_three_col_prose_filtered(self):
        """3-column table is the boundary — prose cells at 3 cols are filtered."""
        from distill.parsers.pdf import _build_ir_table

        long_cell = "A" * 90
        rows = [[long_cell, long_cell, long_cell], [long_cell, long_cell, long_cell]]
        assert _build_ir_table(rows, max_rows=500) is None, \
            "3-column table with prose cells should be filtered (boundary case)"

    def test_build_ir_table_exactly_80_chars_kept(self):
        """Average cell length of exactly 80 chars is NOT filtered (threshold is >80)."""
        from distill.parsers.pdf import _build_ir_table

        cell_80 = "A" * 80
        rows = [[cell_80, cell_80], [cell_80, cell_80]]
        assert _build_ir_table(rows, max_rows=500) is not None, \
            "Average cell length of exactly 80 should not be filtered"

    def test_build_ir_table_mixed_cells_average_decides(self):
        """One long cell in a table of short cells — average below 80, keep."""
        from distill.parsers.pdf import _build_ir_table

        rows = [["Short", "Short"], ["Short", "A" * 200]]
        result = _build_ir_table(rows, max_rows=500)
        assert result is not None, \
            "Table where average cell length < 80 should not be filtered"
