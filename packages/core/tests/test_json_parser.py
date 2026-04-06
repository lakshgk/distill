"""
Tests for distill.parsers.json_parser — JSONParser and _detect_json_type.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.ir import CodeBlock, Document, Paragraph, Section, Table, TextRun
from distill.parsers.json_parser import JSONParser, _detect_json_type
from distill.registry import registry


FIXTURES = Path(__file__).parent / "fixtures"


# ── Type detection ──────────────────────────────────────────────────────────

def test_detect_schema_with_dollar_schema():
    data = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"}
    assert _detect_json_type(data) == "schema"


def test_detect_schema_with_properties_and_type():
    data = {"type": "object", "properties": {"name": {"type": "string"}}}
    assert _detect_json_type(data) == "schema"


def test_detect_schema_with_defs():
    data = {"$defs": {"Address": {"type": "object"}}}
    assert _detect_json_type(data) == "schema"


def test_detect_array_dump():
    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    assert _detect_json_type(data) == "array_dump"


def test_detect_flat_object():
    data = {"name": "Alice", "age": 30, "active": True}
    assert _detect_json_type(data) == "flat_object"


def test_detect_code_for_nested_dict():
    data = {"config": {"database": {"host": "localhost", "port": 5432}}}
    assert _detect_json_type(data) == "code"


def test_detect_code_for_mixed_list():
    data = [1, "two", {"three": 3}]
    assert _detect_json_type(data) == "code"


# ── Schema parsing ──────────────────────────────────────────────────────────

def test_schema_title_in_metadata():
    doc = JSONParser().parse((FIXTURES / "simple_schema.json").read_bytes())
    assert doc.metadata.title is not None
    assert len(doc.metadata.title) > 0


def test_schema_description_in_metadata():
    doc = JSONParser().parse((FIXTURES / "simple_schema.json").read_bytes())
    assert doc.metadata.description is not None
    assert len(doc.metadata.description) > 0


def test_schema_properties_produce_table():
    doc = JSONParser().parse((FIXTURES / "simple_schema.json").read_bytes())
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    # Check table has expected columns: at least name, type
    if tables[0].rows:
        header_texts = [
            "".join(r.text for r in c.content if isinstance(r, TextRun))
            for c in tables[0].rows[0].cells
        ]
        header_lower = [h.lower() for h in header_texts]
        assert any("name" in h for h in header_lower)
        assert any("type" in h for h in header_lower)


def test_schema_defs_produce_sections():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Test",
        "type": "object",
        "properties": {"id": {"type": "integer"}},
        "$defs": {
            "Address": {
                "type": "object",
                "properties": {"street": {"type": "string"}},
            },
            "Phone": {
                "type": "object",
                "properties": {"number": {"type": "string"}},
            },
        },
    }
    doc = JSONParser().parse(json.dumps(schema).encode())
    headings = _collect_heading_texts(doc)
    assert any("Address" in h for h in headings)
    assert any("Phone" in h for h in headings)


def test_schema_required_fields_marked():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
        },
        "required": ["id"],
    }
    doc = JSONParser().parse(json.dumps(schema).encode())
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    # Look for a cell that indicates "required" for id
    all_cell_text = _all_cell_texts(tables[0])
    # At least one cell should reference required status
    assert any("yes" in t.lower() or "required" in t.lower() or "\u2713" in t for t in all_cell_text)


def test_deeply_nested_schema_no_infinite_loop():
    """10-level deep schema should complete without hanging."""
    inner = {"type": "string"}
    for _ in range(10):
        inner = {
            "type": "object",
            "properties": {"nested": inner},
        }
    schema = {"$schema": "http://json-schema.org/draft-07/schema#", **inner}
    doc = JSONParser().parse(json.dumps(schema).encode())
    assert isinstance(doc, Document)


# ── Array dump parsing ──────────────────────────────────────────────────────

def test_array_dump_produces_table():
    doc = JSONParser().parse((FIXTURES / "simple_api_dump.json").read_bytes())
    tables = _collect_tables(doc)
    assert len(tables) >= 1


def test_array_dump_header_count():
    data = [{"a": 1, "b": 2, "c": 3}, {"a": 4, "b": 5, "c": 6}]
    doc = JSONParser().parse(json.dumps(data).encode())
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    # Header row should have 3 cells
    assert len(tables[0].rows[0].cells) == 3


def test_array_dump_row_count():
    """Row count = 1 header + N data rows (header row IS in rows list)."""
    data = [{"x": i} for i in range(5)]
    doc = JSONParser().parse(json.dumps(data).encode())
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    # 1 header row + 5 data rows = 6 total
    assert len(tables[0].rows) == 6


def test_array_exceeding_max_table_rows_emits_warning():
    from distill.parsers.base import ParseOptions
    from distill.warnings import WarningCollector, WarningType

    data = [{"val": i} for i in range(100)]
    collector = WarningCollector()
    options = ParseOptions(max_table_rows=10, collector=collector)
    doc = JSONParser().parse(json.dumps(data).encode(), options=options)
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    assert tables[0].truncated is True
    assert collector.has(WarningType.TABLE_TRUNCATED)


# ── Flat object parsing ────────────────────────────────────────────────────

def test_flat_dict_produces_two_column_table():
    data = {"name": "Alice", "age": 30}
    doc = JSONParser().parse(json.dumps(data).encode())
    tables = _collect_tables(doc)
    assert len(tables) >= 1
    assert len(tables[0].rows[0].cells) == 2


# ── Arbitrary nested JSON → CodeBlock ──────────────────────────────────────

def test_nested_json_produces_codeblock():
    data = {"config": {"db": {"host": "localhost"}}}
    doc = JSONParser().parse(json.dumps(data).encode())
    codeblocks = _collect_codeblocks(doc)
    assert len(codeblocks) >= 1
    assert codeblocks[0].language == "json"


# ── Error handling ──────────────────────────────────────────────────────────

def test_invalid_json_returns_empty_document():
    doc = JSONParser().parse(b"{not valid json!!")
    assert isinstance(doc, Document)


def test_empty_file_returns_empty_document():
    doc = JSONParser().parse(b"")
    assert isinstance(doc, Document)


# ── Metadata ────────────────────────────────────────────────────────────────

def test_metadata_source_format():
    doc = JSONParser().parse((FIXTURES / "simple_schema.json").read_bytes())
    assert doc.metadata.source_format == "json"


def test_metadata_word_count_positive():
    doc = JSONParser().parse((FIXTURES / "simple_schema.json").read_bytes())
    assert doc.metadata.word_count is not None
    assert doc.metadata.word_count > 0


# ── API integration ─────────────────────────────────────────────────────────

def _mock_convert_result():
    from distill.quality import QualityScore

    mock = MagicMock()
    mock.markdown = "# JSON Schema"
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
        source_format="json",
    )
    mock.chunks = None
    mock.document_json = None
    mock.html = None
    mock.extracted = None
    return mock


def test_api_post_json_returns_markdown():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    json_bytes = (FIXTURES / "simple_schema.json").read_bytes()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.json", json_bytes, "application/json")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data


def test_api_post_json_returns_warnings():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    json_bytes = (FIXTURES / "simple_schema.json").read_bytes()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.json", json_bytes, "application/json")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert isinstance(data["warnings"], list)


# ── Helpers ─────────────────────────────────────────────────────────────────

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


def _collect_heading_texts(doc: Document) -> list[str]:
    texts = []
    def _walk(sections):
        for s in sections:
            if s.heading:
                texts.append("".join(r.text for r in s.heading))
            _walk(s.subsections)
    _walk(doc.sections)
    return texts


def _all_cell_texts(table: Table) -> list[str]:
    texts = []
    for row in table.rows:
        for cell in row.cells:
            for item in cell.content:
                if isinstance(item, TextRun):
                    texts.append(item.text)
    return texts
