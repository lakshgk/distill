"""Tests for the IR dataclasses and renderer."""

import pytest
from distill.ir import (
    Document, DocumentMetadata, Image, ImageType,
    List, ListItem, Paragraph, Section, Table, TableCell, TableRow, TextRun,
)
from distill.renderer import MarkdownRenderer


def make_simple_doc() -> Document:
    return Document(
        metadata=DocumentMetadata(title="Test Doc", author="Tester", page_count=1),
        sections=[
            Section(
                heading=[TextRun(text="Introduction")],
                level=1,
                blocks=[
                    Paragraph(runs=[TextRun(text="Hello "), TextRun(text="world", bold=True)]),
                    List(ordered=False, items=[
                        ListItem(content=[TextRun(text="Item one")]),
                        ListItem(content=[TextRun(text="Item two")]),
                    ]),
                ],
            ),
            Section(
                heading=[TextRun(text="Data")],
                level=1,
                blocks=[
                    Table(rows=[
                        TableRow(cells=[
                            TableCell(content=[TextRun(text="Name")], is_header=True),
                            TableCell(content=[TextRun(text="Value")], is_header=True),
                        ]),
                        TableRow(cells=[
                            TableCell(content=[TextRun(text="Alpha")]),
                            TableCell(content=[TextRun(text="1")]),
                        ]),
                    ])
                ],
            ),
        ],
    )


class TestRenderer:
    def test_headings(self):
        doc = make_simple_doc()
        md  = MarkdownRenderer(front_matter=False).render(doc)
        assert "# Introduction" in md
        assert "# Data" in md

    def test_bold_run(self):
        doc = make_simple_doc()
        md  = MarkdownRenderer(front_matter=False).render(doc)
        assert "**world**" in md

    def test_list(self):
        doc = make_simple_doc()
        md  = MarkdownRenderer(front_matter=False).render(doc)
        assert "- Item one" in md
        assert "- Item two" in md

    def test_table(self):
        doc = make_simple_doc()
        md  = MarkdownRenderer(front_matter=False).render(doc)
        assert "| Name" in md
        assert "| Alpha" in md

    def test_front_matter(self):
        doc = make_simple_doc()
        md  = MarkdownRenderer(front_matter=True).render(doc)
        assert "---" in md
        assert "title: 'Test Doc'" in md

    def test_decorative_image_suppressed(self):
        doc = Document(sections=[
            Section(level=0, blocks=[
                Image(image_type=ImageType.DECORATIVE)
            ])
        ])
        md = MarkdownRenderer(front_matter=False).render(doc)
        assert "![" not in md

    def test_image_with_caption(self):
        doc = Document(sections=[
            Section(level=0, blocks=[
                Image(image_type=ImageType.CHART, caption="A bar chart", path="images/fig1.png")
            ])
        ])
        md = MarkdownRenderer(front_matter=False).render(doc)
        assert "![A bar chart](images/fig1.png)" in md


class TestQuality:
    def test_perfect_score_simple_doc(self):
        from distill.quality import score
        doc = make_simple_doc()
        md  = MarkdownRenderer(front_matter=False).render(doc)
        qs  = score(doc, md)
        assert qs.overall > 0.7
        assert qs.heading_preservation == 1.0
        assert qs.table_preservation == 1.0

    def test_empty_document(self):
        from distill.quality import score
        doc = Document()
        md  = ""
        qs  = score(doc, md)
        assert qs.overall is None
        assert qs.error is not None
