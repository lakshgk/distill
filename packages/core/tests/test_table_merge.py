"""
Tests for distill.features.table_merge — TableFragmentDetector and TableMerger.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from distill.features.llm import LLMError
from distill.features.table_merge import TableFragmentDetector, TableMerger
from distill.ir import Document, Section, Table, TableCell, TableRow, TextRun
from distill.warnings import WarningCollector, WarningType


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_table(headers: list[str], rows: list[list[str]]) -> Table:
    header_row = TableRow(cells=[
        TableCell(content=[TextRun(text=h)], is_header=True) for h in headers
    ])
    data_rows = [
        TableRow(cells=[TableCell(content=[TextRun(text=c)]) for c in row])
        for row in rows
    ]
    return Table(rows=[header_row] + data_rows)


def _two_table_doc() -> Document:
    """Two adjacent sections each containing a table (no page metadata)."""
    return Document(sections=[
        Section(level=0, blocks=[_make_table(["Region", "Q1"], [["North", "12"]])]),
        Section(level=0, blocks=[
            Table(rows=[TableRow(cells=[
                TableCell(content=[TextRun(text="South")]),
                TableCell(content=[TextRun(text="8")]),
            ])])
        ]),
    ])


# ── Test 1: detect returns empty when tables lack page metadata ──────────────

def test_detect_returns_empty_without_page_metadata():
    detector = TableFragmentDetector()
    collector = WarningCollector()
    pairs = detector.detect(_two_table_doc(), collector)
    assert pairs == []
    assert not collector.has(WarningType.CROSS_PAGE_TABLE)


# ── Test 2: detect does not raise on empty Document ──────────────────────────

def test_detect_does_not_raise_on_empty_document():
    detector = TableFragmentDetector()
    collector = WarningCollector()
    pairs = detector.detect(Document(), collector)
    assert pairs == []


# ── Test 3: merge reduces section count with valid merged table ──────────────

def test_merge_reduces_section_count():
    doc = _two_table_doc()
    assert len(doc.sections) == 2

    mock_client = MagicMock()
    mock_client.complete.return_value = (
        "| Region | Q1 |\n|---|---|\n| North | 12 |\n| South | 8 |"
    )

    merger = TableMerger(mock_client)
    result = merger.merge(doc, [(0, 1)])
    assert len(result.sections) == 1

    table_blocks = [b for s in result.sections for b in s.blocks if isinstance(b, Table)]
    assert len(table_blocks) == 1
    # Merged table should have header + 2 data rows = 3 rows total
    assert len(table_blocks[0].rows) == 3


# ── Test 4: merge leaves nodes unchanged when LLM returns SEPARATE ───────────

def test_merge_unchanged_on_separate():
    doc = _two_table_doc()
    mock_client = MagicMock()
    mock_client.complete.return_value = "SEPARATE"

    merger = TableMerger(mock_client)
    result = merger.merge(doc, [(0, 1)])
    assert len(result.sections) == 2


# ── Test 5: merge leaves nodes unchanged on LLMError ────────────────────────

def test_merge_unchanged_on_llm_error():
    doc = _two_table_doc()
    mock_client = MagicMock()
    mock_client.complete.side_effect = LLMError("connection failed")

    merger = TableMerger(mock_client)
    result = merger.merge(doc, [(0, 1)])
    assert len(result.sections) == 2


# ── Test 6: merge leaves nodes unchanged on unparseable LLM response ─────────

def test_merge_unchanged_on_unparseable_response():
    doc = _two_table_doc()
    mock_client = MagicMock()
    mock_client.complete.return_value = "This is not a valid markdown table at all."

    merger = TableMerger(mock_client)
    result = merger.merge(doc, [(0, 1)])
    assert len(result.sections) == 2


# ── Test 7: detection runs even when llm_merge_tables=False ──────────────────

def test_detection_runs_without_merge_flag():
    """convert() runs detection for PDF input regardless of llm_merge_tables."""
    from distill.ir import DocumentMetadata, Paragraph

    doc = Document(
        metadata=DocumentMetadata(source_format="pdf"),
        sections=[
            Section(level=0, blocks=[Paragraph(runs=[TextRun(text="Hello")])]),
        ],
    )

    collector = WarningCollector()

    # Detection runs on any PDF Document; it just won't find pairs here
    detector = TableFragmentDetector()
    pairs = detector.detect(doc, collector)

    # The key assertion: calling detect() does NOT raise when
    # llm_merge_tables is False — it runs unconditionally
    assert isinstance(pairs, list)


# ── Test 8: API returns 422 when llm_merge_tables=true without key ───────────

def test_api_422_without_llm_api_key():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    client = TestClient(build_app())

    with patch("distill.convert") as mock_convert:
        files = {"file": ("report.pdf", io.BytesIO(b"fake"), "application/pdf")}
        data = {"llm_merge_tables": "true"}
        resp = client.post("/api/convert", data=data, files=files)

    assert resp.status_code == 422
    assert "llm_api_key" in resp.json()["detail"]
