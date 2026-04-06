"""
Tests for ChunksRenderer (6.2-C).

Covers:
 1. Section with heading + paragraphs produces ≥1 chunk with heading_path set.
 2. Table block always produces a single chunk regardless of row count.
 3. chunk_id values are unique across all chunks for a given document.
 4. Repeated calls with the same input produce identical chunk_ids (idempotency).
 5. A section exceeding 800 estimated tokens is split at Paragraph boundaries.
 6. Parent heading is prepended to child chunk content when a section is split.
 7. API returns chunk_count matching len(chunks).
 8. API returns HTTP 422 for unknown output_format value.
"""

from __future__ import annotations

import pytest
from distill.ir import (
    Document, Section, Paragraph, Table, TableRow, TableCell,
    TextRun, List, ListItem,
)
from distill.renderers.chunks import ChunksRenderer, _MAX_TOKENS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_doc(*sections: Section) -> Document:
    return Document(sections=list(sections))


def _long_paragraph(token_count: int) -> Paragraph:
    """Produce a Paragraph whose rendered text is roughly token_count*4 characters."""
    return Paragraph(runs=[TextRun("x" * (token_count * 4))])


# ── Test 1: heading path is populated ────────────────────────────────────────

def test_section_chunk_has_heading_path():
    doc = _make_doc(
        Section(level=1, heading=[TextRun("Executive Summary")], blocks=[
            Paragraph(runs=[TextRun("Revenue grew 15 percent.")]),
        ]),
    )
    renderer = ChunksRenderer()
    chunks = renderer.render(doc, source_document="report.pdf", source_format="pdf")

    assert len(chunks) >= 1
    assert any("Executive Summary" in c.heading_path for c in chunks)


# ── Test 2: Table is always a single chunk ────────────────────────────────────

def test_table_produces_single_chunk():
    many_rows = [TableRow(cells=[TableCell(content=[TextRun(f"row {i}")])])
                 for i in range(200)]
    table = Table(rows=[
        TableRow(cells=[TableCell(content=[TextRun("Header")], is_header=True)]),
        *many_rows,
    ])
    doc = _make_doc(
        Section(level=1, heading=[TextRun("Data")], blocks=[table]),
    )
    renderer = ChunksRenderer()
    chunks = renderer.render(doc, source_document="data.xlsx", source_format="xlsx")

    table_chunks = [c for c in chunks if c.type == "table"]
    assert len(table_chunks) == 1


# ── Test 3: chunk_id values are unique ───────────────────────────────────────

def test_chunk_ids_are_unique():
    doc = _make_doc(
        Section(level=1, heading=[TextRun("Section A")], blocks=[
            Paragraph(runs=[TextRun("Content A.")]),
            Table(rows=[TableRow(cells=[TableCell(content=[TextRun("T")])])]),
        ]),
        Section(level=1, heading=[TextRun("Section B")], blocks=[
            Paragraph(runs=[TextRun("Content B.")]),
        ]),
    )
    renderer = ChunksRenderer()
    chunks = renderer.render(doc, source_document="doc.docx", source_format="docx")

    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk_ids must be unique"


# ── Test 4: idempotency ───────────────────────────────────────────────────────

def test_chunk_ids_are_stable_across_calls():
    doc = _make_doc(
        Section(level=1, heading=[TextRun("Intro")], blocks=[
            Paragraph(runs=[TextRun("Some content.")]),
        ]),
    )
    renderer = ChunksRenderer()
    first  = [c.chunk_id for c in renderer.render(doc, "stable.pdf", "pdf")]
    second = [c.chunk_id for c in renderer.render(doc, "stable.pdf", "pdf")]
    assert first == second


# ── Test 5: long section is split at Paragraph boundaries ────────────────────

def test_long_section_is_split():
    # Each paragraph is slightly over half the threshold so two together exceed it
    para_tokens = (_MAX_TOKENS // 2) + 50
    doc = _make_doc(
        Section(level=1, heading=[TextRun("Big Section")], blocks=[
            _long_paragraph(para_tokens),
            _long_paragraph(para_tokens),
            _long_paragraph(para_tokens),
        ]),
    )
    renderer = ChunksRenderer()
    chunks = renderer.render(doc, source_document="big.pdf", source_format="pdf")

    # Should produce more than one chunk because combined content exceeds threshold
    assert len(chunks) > 1, (
        f"Expected split into >1 chunk for ~{para_tokens * 3} estimated tokens; got {len(chunks)}"
    )


# ── Test 6: parent heading is prepended to split chunks ──────────────────────

def test_parent_heading_prepended_to_split_chunks():
    para_tokens = (_MAX_TOKENS // 2) + 50
    doc = _make_doc(
        Section(level=1, heading=[TextRun("Parent Heading")], blocks=[
            _long_paragraph(para_tokens),
            _long_paragraph(para_tokens),
            _long_paragraph(para_tokens),
        ]),
    )
    renderer = ChunksRenderer()
    chunks = renderer.render(doc, source_document="split.pdf", source_format="pdf")

    # The first chunk should carry the heading prefix
    assert "Parent Heading" in chunks[0].content, (
        "First split chunk must contain the parent heading"
    )


# ── Test 7 & 8: API-level checks ──────────────────────────────────────────────

@pytest.fixture
def api_client():
    from fastapi.testclient import TestClient
    from distill_app.server import build_app
    return TestClient(build_app())


@pytest.fixture
def simple_docx(tmp_path):
    """Write a minimal valid .docx to a temp file and return its path."""
    import zipfile, textwrap
    docx_path = tmp_path / "simple.docx"

    content_types = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml"
            ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
        </Types>""")

    rels = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
            Target="word/document.xml"/>
        </Relationships>""")

    document_xml = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            <w:p>
              <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
              <w:r><w:t>Test Heading</w:t></w:r>
            </w:p>
            <w:p>
              <w:r><w:t>Hello world paragraph.</w:t></w:r>
            </w:p>
          </w:body>
        </w:document>""")

    word_rels = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        </Relationships>""")

    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", word_rels)

    return docx_path


def test_api_chunk_count_matches_chunks_array(api_client, simple_docx):
    with open(simple_docx, "rb") as f:
        response = api_client.post(
            "/api/convert",
            data={"output_format": "chunks"},
            files={"file": ("simple.docx", f, "application/octet-stream")},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "chunks" in body
    assert "markdown" not in body
    assert body["chunk_count"] == len(body["chunks"])
    assert all("chunk_id" in c for c in body["chunks"])


def test_api_unknown_output_format_returns_422(api_client, simple_docx):
    with open(simple_docx, "rb") as f:
        response = api_client.post(
            "/api/convert",
            data={"output_format": "banana"},
            files={"file": ("simple.docx", f, "application/octet-stream")},
        )
    assert response.status_code == 422
