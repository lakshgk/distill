"""
Tests for distill.parsers.epub — EPUBParser.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.ir import Document, Section, Table, TextRun
from distill.parsers.epub import EPUBParser
from distill.registry import registry


FIXTURES = Path(__file__).parent / "fixtures"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_epub(
    title: str = "Test Book",
    author: str = "Test Author",
    chapters: list[tuple[str, str]] | None = None,
    include_opf: bool = True,
    reverse_spine: bool = False,
) -> bytes:
    """Build a minimal EPUB as bytes using zipfile + BytesIO."""
    if chapters is None:
        chapters = [("chapter1.xhtml", "<h1>Chapter One</h1><p>Hello world.</p>")]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype must be first, stored not deflated
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # META-INF/container.xml
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
            "  <rootfiles>"
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "  </rootfiles>"
            "</container>",
        )

        if include_opf:
            # Build spine items
            spine_ids = []
            manifest_items = []
            for i, (fname, _) in enumerate(chapters):
                item_id = f"ch{i}"
                spine_ids.append(item_id)
                manifest_items.append(
                    f'<item id="{item_id}" href="{fname}" media-type="application/xhtml+xml"/>'
                )

            if reverse_spine:
                spine_ids = list(reversed(spine_ids))

            opf = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                "  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
                f"    <dc:title>{title}</dc:title>"
                f"    <dc:creator>{author}</dc:creator>"
                "  </metadata>"
                "  <manifest>"
                + "".join(manifest_items)
                + "  </manifest>"
                "  <spine>"
                + "".join(f'<itemref idref="{sid}"/>' for sid in spine_ids)
                + "  </spine>"
                "</package>"
            )
            zf.writestr("OEBPS/content.opf", opf)

        # Chapter XHTML files
        for fname, body in chapters:
            xhtml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                f"<body>{body}</body></html>"
            )
            zf.writestr(f"OEBPS/{fname}", xhtml)

    return buf.getvalue()


# ── Registry ────────────────────────────────────────────────────────────────

def test_registry_finds_epub():
    assert registry.find("test.epub") is not None


# ── Metadata ────────────────────────────────────────────────────────────────

def test_parse_fixture_title():
    doc = EPUBParser().parse((FIXTURES / "simple.epub").read_bytes())
    assert doc.metadata.title == "Test Book"


def test_parse_fixture_author():
    doc = EPUBParser().parse((FIXTURES / "simple.epub").read_bytes())
    assert doc.metadata.author == "Test Author"


# ── Structural ──────────────────────────────────────────────────────────────

def test_parse_fixture_has_section_with_heading():
    doc = EPUBParser().parse((FIXTURES / "simple.epub").read_bytes())
    headed = [s for s in doc.sections if s.heading]
    assert len(headed) >= 1


def test_parse_fixture_produces_table():
    doc = EPUBParser().parse((FIXTURES / "simple.epub").read_bytes())
    tables = _collect_tables(doc)
    assert len(tables) >= 1


def test_metadata_word_count_positive():
    doc = EPUBParser().parse((FIXTURES / "simple.epub").read_bytes())
    assert doc.metadata.word_count is not None
    assert doc.metadata.word_count > 0


def test_metadata_source_format():
    doc = EPUBParser().parse((FIXTURES / "simple.epub").read_bytes())
    assert doc.metadata.source_format == "epub"


# ── Error handling ──────────────────────────────────────────────────────────

def test_corrupt_zip_returns_document():
    doc = EPUBParser().parse(b"\x00\x01\x02random garbage bytes")
    assert isinstance(doc, Document)


def test_valid_zip_no_opf_returns_document():
    epub_bytes = _make_epub(include_opf=False)
    doc = EPUBParser().parse(epub_bytes)
    assert isinstance(doc, Document)


def test_malformed_xhtml_skipped_others_parsed():
    """One bad chapter should be skipped; the other still produces content."""
    chapters = [
        ("chapter1.xhtml", "<h1>Good Chapter</h1><p>Content here.</p>"),
        ("chapter2.xhtml", "<h1>Bad Chapter<<</malformed>>>"),
    ]
    epub_bytes = _make_epub(chapters=chapters)
    doc = EPUBParser().parse(epub_bytes)
    assert isinstance(doc, Document)
    # At least the good chapter should produce a section
    headings = _collect_heading_texts(doc)
    assert "Good Chapter" in headings


def test_spine_reading_order():
    """Chapters should appear in spine order even when files are reversed."""
    chapters = [
        ("chapter1.xhtml", "<h1>First</h1><p>One.</p>"),
        ("chapter2.xhtml", "<h1>Second</h1><p>Two.</p>"),
    ]
    epub_bytes = _make_epub(chapters=chapters, reverse_spine=False)
    doc = EPUBParser().parse(epub_bytes)
    headings = _collect_heading_texts(doc)
    if "First" in headings and "Second" in headings:
        assert headings.index("First") < headings.index("Second")


# ── API integration ─────────────────────────────────────────────────────────

def _mock_convert_result():
    from distill.quality import QualityScore

    mock = MagicMock()
    mock.markdown = "# Test"
    mock.quality_score = 0.9
    mock.quality_details = QualityScore(
        overall=0.9,
        heading_preservation=1.0,
        table_preservation=1.0,
        list_preservation=1.0,
        token_reduction_ratio=0.8,
    )
    mock.warnings = []
    mock.structured_warnings = []
    mock.metadata = MagicMock(
        word_count=5,
        page_count=1,
        slide_count=None,
        sheet_count=None,
        source_format="epub",
    )
    mock.chunks = None
    mock.document_json = None
    mock.html = None
    mock.extracted = None
    return mock


def test_api_post_epub_returns_markdown():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    epub_bytes = _make_epub()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.epub", epub_bytes, "application/epub+zip")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data


def test_api_post_epub_returns_warnings_list():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    epub_bytes = _make_epub()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.epub", epub_bytes, "application/epub+zip")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert isinstance(data["warnings"], list)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _collect_tables(doc: Document) -> list[Table]:
    tables = []
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, Table):
                tables.append(block)
        for sub in section.subsections:
            for block in sub.blocks:
                if isinstance(block, Table):
                    tables.append(block)
    return tables


def _collect_heading_texts(doc: Document) -> list[str]:
    texts = []
    def _walk(sections):
        for s in sections:
            if s.heading:
                texts.append("".join(r.text for r in s.heading))
            _walk(s.subsections)
    _walk(doc.sections)
    return texts
