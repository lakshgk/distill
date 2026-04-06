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

from distill.ir import Document, Paragraph, Section, Table
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


# ── Word count ────────────────────────────────────────────────────────────────

class TestWordCount:
    def test_native_pdf_word_count_populated(self):
        data = _make_pdf_bytes("Hello from Distill testing word count.")
        doc = PdfParser().parse(data)
        assert doc.metadata.word_count is not None
        assert doc.metadata.word_count > 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _all_text(doc: Document) -> str:
    parts: list[str] = []
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, Paragraph):
                for run in block.runs:
                    parts.append(run.text)
    return " ".join(parts)
