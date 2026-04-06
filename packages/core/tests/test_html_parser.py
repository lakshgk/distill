"""
Tests for distill.parsers.html — HTMLContentExtractor and HTMLParser.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.ir import List, Table
from distill.parsers.html import HTMLContentExtractor, HTMLParser


FIXTURES = Path(__file__).parent / "fixtures"


# ── HTMLContentExtractor ─────────────────────────────────────────────────────

def test_extractor_passthrough_returns_raw_html():
    raw = "<html><body><p>Hello</p></body></html>"
    extractor = HTMLContentExtractor()
    assert extractor.extract(raw, extract_content=False) == raw


def test_extractor_with_extract_content_does_not_raise():
    raw = "<nav>Nav</nav><article><h1>Title</h1><p>Body text.</p></article>"
    extractor = HTMLContentExtractor()
    result = extractor.extract(raw, extract_content=True)
    assert isinstance(result, str)
    assert len(result) > 0


def test_extractor_malformed_html_does_not_raise():
    extractor = HTMLContentExtractor()
    result = extractor.extract("<<<not valid html>>>", extract_content=False)
    assert isinstance(result, str)


# ── HTMLParser — structural mapping ─────────────────────────────────────────

def test_parser_all_six_heading_levels():
    html = (
        "<h1>H1</h1><h2>H2</h2><h3>H3</h3>"
        "<h4>H4</h4><h5>H5</h5><h6>H6</h6>"
    )
    doc = HTMLParser().parse(html.encode())
    levels = [s.level for s in doc.sections]
    assert levels == [1, 2, 3, 4, 5, 6]


def _doc(body: str) -> bytes:
    """Wrap a body fragment in a full HTML document so lxml returns <html> as root."""
    return f"<!DOCTYPE html><html><body>{body}</body></html>".encode()


def test_parser_heading_text_is_preserved():
    doc = HTMLParser().parse(_doc("<h1>Hello World</h1>"))
    heading_text = "".join(r.text for r in doc.sections[0].heading)
    assert heading_text == "Hello World"


def test_parser_table_with_explicit_thead_marks_headers():
    html = (
        "<table>"
        "<thead><tr><th>Name</th><th>Val</th></tr></thead>"
        "<tbody><tr><td>A</td><td>1</td></tr></tbody>"
        "</table>"
    )
    doc = HTMLParser().parse(_doc(html))
    table = _first_table(doc)
    assert table is not None
    assert all(c.is_header for c in table.rows[0].cells)
    assert not any(c.is_header for c in table.rows[1].cells)


def test_parser_table_without_thead_treats_first_row_as_header():
    html = (
        "<table>"
        "<tr><td>Col A</td><td>Col B</td></tr>"
        "<tr><td>1</td><td>2</td></tr>"
        "</table>"
    )
    doc = HTMLParser().parse(_doc(html))
    table = _first_table(doc)
    assert table is not None
    assert all(c.is_header for c in table.rows[0].cells)


def test_parser_nested_lists_three_levels():
    html = (
        "<ul>"
        "  <li>Level 1"
        "    <ul><li>Level 2"
        "      <ul><li>Level 3</li></ul>"
        "    </li></ul>"
        "  </li>"
        "</ul>"
    )
    doc = HTMLParser().parse(_doc(html))
    lst = _first_list(doc)
    assert lst is not None
    assert lst.items[0].children, "expected nested list at level 2"
    nested2 = lst.items[0].children[0]
    assert nested2.items[0].children, "expected nested list at level 3"


def test_parser_unknown_tags_do_not_raise():
    html = "<foo><bar>Some content</bar></foo>"
    doc = HTMLParser().parse(html.encode())
    assert doc is not None


def test_parser_empty_html_returns_document():
    doc = HTMLParser().parse(b"")
    assert doc is not None
    assert isinstance(doc.sections, list)


# ── Full pipeline ────────────────────────────────────────────────────────────

def test_pipeline_html_file_to_markdown():
    from distill import convert
    result = convert(str(FIXTURES / "simple.html"))
    assert result.quality_score > 0
    assert "Main Heading" in result.markdown


def test_pipeline_html_file_output_format_chunks():
    from distill import convert, ParseOptions
    result = convert(
        str(FIXTURES / "simple.html"),
        options=ParseOptions(output_format="chunks"),
    )
    assert result.chunks is not None
    assert len(result.chunks) > 0
    assert any(c.heading_path for c in result.chunks)


def test_pipeline_html_tmp_file(tmp_path):
    html_file = tmp_path / "doc.html"
    html_file.write_text(
        "<h1>Intro</h1><p>Some text here.</p>",
        encoding="utf-8",
    )
    from distill import convert
    result = convert(str(html_file))
    assert "Intro" in result.markdown
    assert result.quality_score > 0


# ── CONTENT_EXTRACTED warning ────────────────────────────────────────────────

def test_content_extracted_warning_when_both_extractors_fail():
    from distill.warnings import WarningCollector, WarningType

    collector = WarningCollector()
    extractor = HTMLContentExtractor(collector=collector)

    bad_trafilatura = MagicMock()
    bad_trafilatura.extract.side_effect = RuntimeError("forced trafilatura failure")

    bad_readability_doc = MagicMock(side_effect=RuntimeError("forced readability failure"))
    bad_readability = MagicMock()
    bad_readability.Document = bad_readability_doc

    with patch.dict("sys.modules", {
        "trafilatura": bad_trafilatura,
        "readability": bad_readability,
    }):
        result = extractor.extract("<p>test</p>", extract_content=True)

    assert result == "<p>test</p>"
    assert collector.has(WarningType.CONTENT_EXTRACTED)


# ── Word count ───────────────────────────────────────────────────────────────

def test_parser_sets_word_count():
    doc = HTMLParser().parse((FIXTURES / "simple.html").read_bytes())
    assert doc.metadata.word_count is not None
    assert doc.metadata.word_count > 0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _first_table(doc):
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, Table):
                return block
    return None


def _first_list(doc):
    for section in doc.sections:
        for block in section.blocks:
            if isinstance(block, List):
                return block
    return None
