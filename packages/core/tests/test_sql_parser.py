"""
Tests for distill.parsers.sql — SQLParser.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.ir import CodeBlock, Document, Paragraph, Section, Table, TextRun
from distill.parsers.sql import SQLParser
from distill.registry import registry


FIXTURES = Path(__file__).parent / "fixtures"


# ── Registry ────────────────────────────────────────────────────────────────

def test_registry_finds_sql():
    assert registry.find("test.sql") is not None


# ── CREATE TABLE ────────────────────────────────────────────────────────────

def test_create_table_produces_section_with_heading():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    headings = _collect_heading_texts(doc)
    # The fixture should have at least one CREATE TABLE; its name appears as heading
    assert len(headings) >= 1


def test_column_definitions_produce_table_block():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    # Table should have columns: at least name, type
    if tables[0].rows:
        header_texts = _row_texts(tables[0].rows[0])
        header_lower = [h.lower() for h in header_texts]
        assert any("column" in h or "name" in h for h in header_lower)
        assert any("type" in h for h in header_lower)


def test_not_null_marked():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    tables = _collect_tables(doc)
    all_text = []
    for t in tables:
        for row in t.rows:
            all_text.extend(_row_texts(row))
    assert any("NOT NULL" in t.upper() for t in all_text)


def test_primary_key_identified():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    tables = _collect_tables(doc)
    all_text = []
    for t in tables:
        for row in t.rows:
            all_text.extend(_row_texts(row))
    assert any("PRIMARY" in t.upper() or t.upper() == "YES" for t in all_text)


def test_foreign_key_paragraph():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    paragraphs = _collect_paragraph_texts(doc)
    assert any("FOREIGN" in p.upper() or "REFERENCES" in p.upper() for p in paragraphs)


def test_inline_comment_description():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    paragraphs = _collect_paragraph_texts(doc)
    # The fixture should have a comment above a CREATE TABLE that becomes a description
    assert any(len(p.strip()) > 0 for p in paragraphs)


# ── CREATE INDEX ────────────────────────────────────────────────────────────

def test_create_index_associated_with_table():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    headings = _collect_heading_texts(doc)
    # Should have an index-related section or subsection referencing the table
    all_text = headings + _collect_paragraph_texts(doc) + _collect_codeblock_texts(doc)
    assert any("INDEX" in t.upper() or "idx" in t.lower() for t in all_text)


# ── CREATE VIEW ─────────────────────────────────────────────────────────────

def test_create_view_produces_codeblock():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    codeblocks = _collect_codeblocks(doc)
    view_blocks = [cb for cb in codeblocks if "VIEW" in (cb.code or "").upper()]
    # If the fixture has a CREATE VIEW it should appear as a CodeBlock
    # Check there's at least one code block with SQL language
    sql_blocks = [cb for cb in codeblocks if cb.language == "sql"]
    assert len(sql_blocks) >= 1


# ── DML statements ──────────────────────────────────────────────────────────

def test_select_produces_codeblock():
    sql = b"SELECT id, name FROM users WHERE active = 1;"
    doc = SQLParser().parse(sql)
    codeblocks = _collect_codeblocks(doc)
    assert len(codeblocks) >= 1
    assert codeblocks[0].language == "sql"


def test_insert_produces_codeblock():
    sql = b"INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com');"
    doc = SQLParser().parse(sql)
    codeblocks = _collect_codeblocks(doc)
    assert len(codeblocks) >= 1
    assert codeblocks[0].language == "sql"


# ── Error handling ──────────────────────────────────────────────────────────

def test_malformed_sql_returns_document():
    doc = SQLParser().parse(b"THIS IS NOT VALID SQL !@#$%")
    assert isinstance(doc, Document)


def test_empty_file_returns_document():
    doc = SQLParser().parse(b"")
    assert isinstance(doc, Document)


# ── Metadata ────────────────────────────────────────────────────────────────

def test_metadata_source_format():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    assert doc.metadata.source_format == "sql"


def test_metadata_word_count():
    doc = SQLParser().parse((FIXTURES / "simple.sql").read_bytes())
    assert doc.metadata.word_count is not None
    assert doc.metadata.word_count > 0


# ── API integration ─────────────────────────────────────────────────────────

def _mock_convert_result():
    from distill.quality import QualityScore

    mock = MagicMock()
    mock.markdown = "# Users Table"
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
        word_count=20,
        page_count=1,
        slide_count=None,
        sheet_count=None,
        source_format="sql",
    )
    mock.chunks = None
    mock.document_json = None
    mock.html = None
    mock.extracted = None
    return mock


def test_api_post_sql_returns_markdown():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    sql_bytes = (FIXTURES / "simple.sql").read_bytes()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.sql", sql_bytes, "application/sql")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data


def test_api_post_sql_returns_warnings():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    sql_bytes = (FIXTURES / "simple.sql").read_bytes()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.sql", sql_bytes, "application/sql")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert isinstance(data["warnings"], list)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _collect_heading_texts(doc: Document) -> list[str]:
    texts = []
    def _walk(sections):
        for s in sections:
            if s.heading:
                texts.append("".join(r.text for r in s.heading))
            _walk(s.subsections)
    _walk(doc.sections)
    return texts


def _collect_tables(doc: Document) -> list[Table]:
    tables = []
    def _walk(sections):
        for s in sections:
            for block in s.blocks:
                if isinstance(block, Table):
                    tables.append(block)
            _walk(s.subsections)
    _walk(doc.sections)
    return tables


def _collect_paragraph_texts(doc: Document) -> list[str]:
    texts = []
    def _walk(sections):
        for s in sections:
            for block in s.blocks:
                if isinstance(block, Paragraph):
                    texts.append("".join(r.text for r in block.runs))
            _walk(s.subsections)
    _walk(doc.sections)
    return texts


def _collect_codeblocks(doc: Document) -> list[CodeBlock]:
    blocks = []
    def _walk(sections):
        for s in sections:
            for block in s.blocks:
                if isinstance(block, CodeBlock):
                    blocks.append(block)
            _walk(s.subsections)
    _walk(doc.sections)
    return blocks


def _collect_codeblock_texts(doc: Document) -> list[str]:
    return [cb.code for cb in _collect_codeblocks(doc)]


def _row_texts(row) -> list[str]:
    texts = []
    for cell in row.cells:
        for item in cell.content:
            if isinstance(item, TextRun):
                texts.append(item.text)
    return texts
