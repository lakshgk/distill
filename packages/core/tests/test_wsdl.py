"""
Tests for distill.parsers.wsdl — WSDLParser.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from distill.ir import Document, Paragraph, Section, TextRun
from distill.parsers.wsdl import WSDLParser
from distill.registry import registry


FIXTURES = Path(__file__).parent / "fixtures"


# ── Registry ────────────────────────────────────────────────────────────────

def test_registry_finds_wsdl():
    assert registry.find("test.wsdl") is not None


def test_registry_wsd_routes_to_same_parser():
    cls_wsdl = registry.find("test.wsdl")
    cls_wsd = registry.find("test.wsd")
    assert cls_wsdl is cls_wsd


# ── Structural ──────────────────────────────────────────────────────────────

def test_parse_produces_sections():
    doc = WSDLParser().parse((FIXTURES / "simple.wsdl").read_bytes())
    assert len(doc.sections) >= 1


def test_order_service_in_heading():
    doc = WSDLParser().parse((FIXTURES / "simple.wsdl").read_bytes())
    headings = _collect_heading_texts(doc)
    assert any("OrderService" in h for h in headings)


def test_operation_or_porttype_in_content():
    doc = WSDLParser().parse((FIXTURES / "simple.wsdl").read_bytes())
    headings = _collect_heading_texts(doc)
    paragraphs = _collect_paragraph_texts(doc)
    all_text = headings + paragraphs
    assert any("GetOrder" in t or "OrderPortType" in t for t in all_text)


def test_documentation_text_in_paragraph():
    doc = WSDLParser().parse((FIXTURES / "simple.wsdl").read_bytes())
    paragraphs = _collect_paragraph_texts(doc)
    # The fixture should include a <documentation> element whose text appears
    assert any(len(p.strip()) > 0 for p in paragraphs)


# ── Metadata ────────────────────────────────────────────────────────────────

def test_metadata_word_count():
    doc = WSDLParser().parse((FIXTURES / "simple.wsdl").read_bytes())
    assert doc.metadata.word_count is not None
    assert doc.metadata.word_count > 0


def test_metadata_source_format():
    doc = WSDLParser().parse((FIXTURES / "simple.wsdl").read_bytes())
    assert doc.metadata.source_format == "wsdl"


# ── Error handling ──────────────────────────────────────────────────────────

def test_malformed_xml_returns_document():
    doc = WSDLParser().parse(b"<<<not valid xml at all>>>")
    assert isinstance(doc, Document)


def test_empty_file_returns_document():
    doc = WSDLParser().parse(b"")
    assert isinstance(doc, Document)


def test_wsdl_20_namespace_no_error():
    """WSDL 2.0 uses a different namespace — parser should not crash."""
    wsdl2 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<description xmlns="http://www.w3.org/ns/wsdl"'
        '  targetNamespace="http://example.com/wsdl20">'
        "  <interface name=\"TestInterface\">"
        "    <operation name=\"TestOp\" />"
        "  </interface>"
        "</description>"
    ).encode()
    doc = WSDLParser().parse(wsdl2)
    assert isinstance(doc, Document)


def test_partial_wsdl_missing_service():
    """WSDL without a <service> element should still produce output."""
    partial = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"'
        '  xmlns:tns="http://example.com" targetNamespace="http://example.com"'
        '  name="PartialService">'
        "  <portType name=\"SomePortType\">"
        "    <operation name=\"SomeOp\">"
        "      <documentation>Partial doc test.</documentation>"
        "    </operation>"
        "  </portType>"
        "</definitions>"
    ).encode()
    doc = WSDLParser().parse(partial)
    assert isinstance(doc, Document)
    # Should still have at least one section
    assert len(doc.sections) >= 1


# ── API integration ─────────────────────────────────────────────────────────

def _mock_convert_result():
    from distill.quality import QualityScore

    mock = MagicMock()
    mock.markdown = "# OrderService"
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
        word_count=10,
        page_count=1,
        slide_count=None,
        sheet_count=None,
        source_format="wsdl",
    )
    mock.chunks = None
    mock.document_json = None
    mock.html = None
    mock.extracted = None
    return mock


def test_api_post_wsdl_returns_markdown():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app

    app = build_app()
    client = TestClient(app)

    wsdl_bytes = (FIXTURES / "simple.wsdl").read_bytes()

    with patch("distill.convert", return_value=_mock_convert_result()):
        resp = client.post(
            "/api/convert",
            files={"file": ("test.wsdl", wsdl_bytes, "application/xml")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "markdown" in data


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
