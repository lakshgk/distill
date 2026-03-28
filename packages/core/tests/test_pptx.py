"""
Tests for distill.parsers.pptx — PptxParser, PptLegacyParser, and helpers.

Fixtures are built programmatically via python-pptx; no binary files are
checked into the repository.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from distill.ir import BlockQuote, Document, List, Paragraph, Section, Table
from distill.parsers.base import ParseError
from distill.parsers.pptx import (
    PptLegacyParser,
    PptxParser,
    _check_input_size,
    _check_zip_bomb,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _make_pptx(
    *,
    slides: list[dict] | None = None,
    title: str = "",
    author: str = "",
    subject: str = "",
    description: str = "",
    keywords: str = "",
) -> bytes:
    """
    Build a minimal .pptx in memory.

    Each slide dict may contain:
        title    (str)
        body     (str)           — body text in content placeholder
        bullets  (list[str])     — bullet items (level 1)
        notes    (str)           — speaker notes
        table    (list[list])    — rows×cols data (first row = header)
    """
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()

    default_slides = [{"title": "Slide One", "body": "Hello world."}] if slides is None else slides
    for slide_def in default_slides:
        if slide_def.get("table"):
            layout = prs.slide_layouts[5]   # Blank
            sl = prs.slides.add_slide(layout)
            if slide_def.get("title"):
                tb = sl.shapes.add_textbox(Inches(0), Inches(0), Inches(8), Inches(0.5))
                tb.text_frame.text = slide_def["title"]
            data = slide_def["table"]
            rows_count = len(data)
            cols_count = len(data[0]) if data else 1
            tbl_shape = sl.shapes.add_table(
                rows_count, cols_count,
                Inches(0.5), Inches(1), Inches(8), Inches(rows_count * 0.5),
            )
            for r, row in enumerate(data):
                for c, val in enumerate(row):
                    tbl_shape.table.cell(r, c).text = str(val)
        else:
            layout = prs.slide_layouts[1]   # Title and Content
            sl = prs.slides.add_slide(layout)
            if slide_def.get("title"):
                sl.shapes.title.text = slide_def["title"]
            if slide_def.get("body") or slide_def.get("bullets"):
                ph = sl.placeholders[1]
                tf = ph.text_frame
                if slide_def.get("body"):
                    tf.text = slide_def["body"]
                for bullet in (slide_def.get("bullets") or []):
                    p = tf.add_paragraph()
                    p.text  = bullet
                    p.level = 1

        if slide_def.get("notes"):
            sl.notes_slide.notes_text_frame.text = slide_def["notes"]

    prs.core_properties.title       = title
    prs.core_properties.author      = author
    prs.core_properties.subject     = subject
    prs.core_properties.description = description
    prs.core_properties.keywords    = keywords

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _all_texts(doc: Document) -> list[str]:
    texts: list[str] = []
    for section in doc.sections:
        for run in (section.heading or []):
            texts.append(run.text)
        for block in section.blocks:
            if isinstance(block, Paragraph):
                texts.extend(r.text for r in block.runs)
            elif isinstance(block, List):
                texts.extend(r.text for item in block.items for r in item.content)
            elif isinstance(block, BlockQuote):
                for b in block.content:
                    if isinstance(b, Paragraph):
                        texts.extend(r.text for r in b.runs)
    return texts


def _tables(doc: Document) -> list[Table]:
    return [
        block
        for section in doc.sections
        for block in section.blocks
        if isinstance(block, Table)
    ]


def _blockquotes(doc: Document) -> list[BlockQuote]:
    return [
        block
        for section in doc.sections
        for block in section.blocks
        if isinstance(block, BlockQuote)
    ]


# ── Parser availability ───────────────────────────────────────────────────────

class TestParserAvailability:
    def test_pptx_is_available(self):
        assert PptxParser.is_available()

    def test_pptx_extensions(self):
        assert ".pptx" in PptxParser.extensions

    def test_pptx_missing_requires_empty(self):
        assert PptxParser.missing_requires() == []

    def test_ppt_legacy_is_available(self):
        assert PptLegacyParser.is_available()

    def test_ppt_legacy_extension(self):
        assert ".ppt" in PptLegacyParser.extensions


# ── Basic parsing ─────────────────────────────────────────────────────────────

class TestBasicParsing:
    def test_returns_document(self):
        data = _make_pptx()
        doc  = PptxParser().parse(data)
        assert isinstance(doc, Document)

    def test_one_section_per_slide(self):
        data = _make_pptx(slides=[
            {"title": "Intro"},
            {"title": "Details"},
            {"title": "Summary"},
        ])
        doc = PptxParser().parse(data)
        assert len(doc.sections) == 3

    def test_section_level_is_2(self):
        data = _make_pptx()
        doc  = PptxParser().parse(data)
        assert all(s.level == 2 for s in doc.sections)

    def test_slide_title_in_heading(self):
        data = _make_pptx(slides=[{"title": "Annual Review"}])
        doc  = PptxParser().parse(data)
        heading = " ".join(r.text for r in doc.sections[0].heading)
        assert "Annual Review" in heading

    def test_slide_number_in_heading(self):
        data = _make_pptx(slides=[{"title": "Revenue"}, {"title": "Costs"}])
        doc  = PptxParser().parse(data)
        h0 = " ".join(r.text for r in doc.sections[0].heading)
        h1 = " ".join(r.text for r in doc.sections[1].heading)
        assert "Slide 1" in h0
        assert "Slide 2" in h1

    def test_untitled_slide_heading(self):
        data = _make_pptx(slides=[{"body": "No title here"}])
        doc  = PptxParser().parse(data)
        heading = " ".join(r.text for r in doc.sections[0].heading)
        assert "Slide 1" in heading

    def test_body_text_extracted(self):
        data = _make_pptx(slides=[{"title": "T", "body": "Main content here."}])
        doc  = PptxParser().parse(data)
        texts = _all_texts(doc)
        assert any("Main content here." in t for t in texts)

    def test_accepts_path(self, tmp_path):
        p = tmp_path / "test.pptx"
        p.write_bytes(_make_pptx())
        doc = PptxParser().parse(str(p))
        assert isinstance(doc, Document)

    def test_accepts_path_object(self, tmp_path):
        p = tmp_path / "test.pptx"
        p.write_bytes(_make_pptx())
        doc = PptxParser().parse(p)
        assert isinstance(doc, Document)

    def test_empty_presentation_no_crash(self):
        data = _make_pptx(slides=[])
        doc  = PptxParser().parse(data)
        assert isinstance(doc, Document)
        assert len(doc.sections) == 0


# ── Bullet lists ──────────────────────────────────────────────────────────────

class TestBulletLists:
    def test_bullets_become_list(self):
        data = _make_pptx(slides=[{
            "title": "Bullets",
            "bullets": ["Point A", "Point B", "Point C"],
        }])
        doc   = PptxParser().parse(data)
        lists = [b for b in doc.sections[0].blocks if isinstance(b, List)]
        assert len(lists) >= 1

    def test_bullet_texts_extracted(self):
        data = _make_pptx(slides=[{
            "title": "Bullets",
            "bullets": ["Alpha", "Beta", "Gamma"],
        }])
        doc  = PptxParser().parse(data)
        texts = _all_texts(doc)
        assert any("Alpha" in t for t in texts)
        assert any("Beta"  in t for t in texts)
        assert any("Gamma" in t for t in texts)


# ── Speaker notes ─────────────────────────────────────────────────────────────

class TestSpeakerNotes:
    def test_notes_become_blockquote(self):
        data = _make_pptx(slides=[{
            "title": "T",
            "body": "Content.",
            "notes": "These are speaker notes.",
        }])
        doc = PptxParser().parse(data)
        bqs = _blockquotes(doc)
        assert len(bqs) == 1

    def test_notes_text_in_blockquote(self):
        data = _make_pptx(slides=[{
            "title": "T",
            "notes": "Important talking point.",
        }])
        doc   = PptxParser().parse(data)
        texts = _all_texts(doc)
        assert any("Important talking point." in t for t in texts)

    def test_no_notes_no_blockquote(self):
        data = _make_pptx(slides=[{"title": "T", "body": "No notes here."}])
        doc  = PptxParser().parse(data)
        assert len(_blockquotes(doc)) == 0

    def test_notes_only_on_correct_slide(self):
        data = _make_pptx(slides=[
            {"title": "A", "notes": "Notes for A."},
            {"title": "B"},
        ])
        doc = PptxParser().parse(data)
        assert len(_blockquotes(doc)) == 1
        bq_text = " ".join(
            r.text for b in _blockquotes(doc) for p in b.content
            if isinstance(p, Paragraph) for r in p.runs
        )
        assert "Notes for A" in bq_text


# ── Tables ────────────────────────────────────────────────────────────────────

class TestTables:
    def test_table_extracted(self):
        data = _make_pptx(slides=[{
            "title": "Data",
            "table": [["Region", "Sales"], ["North", "500"], ["South", "300"]],
        }])
        doc  = PptxParser().parse(data)
        tbls = _tables(doc)
        assert len(tbls) == 1

    def test_table_first_row_is_header(self):
        data = _make_pptx(slides=[{
            "table": [["H1", "H2"], ["r1", "r2"]],
        }])
        doc  = PptxParser().parse(data)
        tbls = _tables(doc)
        assert all(c.is_header for c in tbls[0].rows[0].cells)

    def test_table_data_rows_not_header(self):
        data = _make_pptx(slides=[{
            "table": [["H1", "H2"], ["r1", "r2"]],
        }])
        doc  = PptxParser().parse(data)
        tbls = _tables(doc)
        assert all(not c.is_header for c in tbls[0].rows[1].cells)

    def test_table_cell_values(self):
        data = _make_pptx(slides=[{
            "table": [["City", "Pop"], ["Oslo", "700000"]],
        }])
        doc  = PptxParser().parse(data)
        tbls = _tables(doc)
        texts = [r.text for row in tbls[0].rows for c in row.cells for r in c.content]
        assert "City"   in texts
        assert "Oslo"   in texts
        assert "700000" in texts

    def test_table_row_cap(self):
        from distill.parsers.base import ParseOptions
        rows = [["H"]] + [[str(i)] for i in range(50)]
        data = _make_pptx(slides=[{"table": rows}])
        opts = ParseOptions(max_table_rows=5)
        doc  = PptxParser().parse(data, options=opts)
        tbls = _tables(doc)
        assert len(tbls[0].rows) == 5


# ── Metadata ──────────────────────────────────────────────────────────────────

class TestMetadata:
    def test_source_format(self):
        doc = PptxParser().parse(_make_pptx())
        assert doc.metadata.source_format == "pptx"

    def test_slide_count(self):
        data = _make_pptx(slides=[{"title": "A"}, {"title": "B"}])
        doc  = PptxParser().parse(data)
        assert doc.metadata.slide_count == 2

    def test_word_count_positive(self):
        data = _make_pptx(slides=[{"title": "T", "body": "One two three four five."}])
        doc  = PptxParser().parse(data)
        assert doc.metadata.word_count is not None
        assert doc.metadata.word_count > 0

    def test_title(self, tmp_path):
        p = tmp_path / "t.pptx"
        p.write_bytes(_make_pptx(title="My Deck"))
        doc = PptxParser().parse(p)
        assert doc.metadata.title == "My Deck"

    def test_author(self, tmp_path):
        p = tmp_path / "t.pptx"
        p.write_bytes(_make_pptx(author="Jane Doe"))
        doc = PptxParser().parse(p)
        assert doc.metadata.author == "Jane Doe"

    def test_subject(self, tmp_path):
        p = tmp_path / "t.pptx"
        p.write_bytes(_make_pptx(subject="Q4 Review"))
        doc = PptxParser().parse(p)
        assert doc.metadata.subject == "Q4 Review"

    def test_keywords_comma(self, tmp_path):
        p = tmp_path / "t.pptx"
        p.write_bytes(_make_pptx(keywords="finance, growth, q4"))
        doc = PptxParser().parse(p)
        assert "finance" in doc.metadata.keywords
        assert "growth"  in doc.metadata.keywords

    def test_keywords_semicolon(self, tmp_path):
        p = tmp_path / "t.pptx"
        p.write_bytes(_make_pptx(keywords="alpha;beta;gamma"))
        doc = PptxParser().parse(p)
        assert doc.metadata.keywords == ["alpha", "beta", "gamma"]


# ── Security ──────────────────────────────────────────────────────────────────

class TestSecurity:
    def test_input_size_bytes_rejected(self):
        oversized = b"x" * (55 * 1024 * 1024)
        with pytest.raises(ParseError, match="50 MB"):
            _check_input_size(oversized, 50 * 1024 * 1024)

    def test_input_size_path_rejected(self, tmp_path):
        big = tmp_path / "big.pptx"
        big.write_bytes(b"x" * (55 * 1024 * 1024))
        with pytest.raises(ParseError, match="50 MB"):
            _check_input_size(str(big), 50 * 1024 * 1024)

    def test_input_size_ok(self):
        _check_input_size(b"tiny", 50 * 1024 * 1024)  # must not raise

    def test_zip_bomb_detected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.bin", "A" * (501 * 1024 * 1024))
        with pytest.raises(ParseError, match="500 MB"):
            _check_zip_bomb(buf.getvalue(), 500 * 1024 * 1024)

    def test_bad_zip_raises(self):
        with pytest.raises(ParseError, match="not a valid PPTX"):
            _check_zip_bomb(b"not a zip file", 500 * 1024 * 1024)

    def test_parser_rejects_oversized(self):
        oversized = b"x" * (55 * 1024 * 1024)
        with pytest.raises(ParseError, match="50 MB"):
            PptxParser().parse(oversized)

    def test_custom_size_limit(self):
        from distill.parsers.base import ParseOptions
        data = b"x" * (15 * 1024 * 1024)
        opts = ParseOptions(extra={"max_file_size": 10 * 1024 * 1024})
        with pytest.raises(ParseError, match="10 MB"):
            PptxParser().parse(data, options=opts)

    def test_garbled_bytes_raises_parse_error(self):
        with pytest.raises(ParseError):
            PptxParser().parse(b"this is not a pptx")


# ── PptLegacyParser ───────────────────────────────────────────────────────────

class TestPptLegacyParser:
    def test_raises_parse_error(self):
        # Wired to LibreOffice — will fail due to binary not found or conversion error
        with pytest.raises(ParseError):
            PptLegacyParser().parse(b"garbage")

    def test_error_mentions_libreoffice(self):
        with pytest.raises(ParseError, match="LibreOffice"):
            PptLegacyParser().parse(b"garbage")

    def test_extension_registered(self):
        assert ".ppt" in PptLegacyParser.extensions


# ── Render integration ─────────────────────────────────────────────────────────

class TestRenderIntegration:
    def test_renders_to_markdown(self):
        data = _make_pptx(slides=[{"title": "Intro", "body": "Content here."}])
        doc  = PptxParser().parse(data)
        md   = doc.render(front_matter=False)
        assert isinstance(md, str)
        assert len(md) > 0

    def test_front_matter_has_source_format(self):
        data = _make_pptx()
        doc  = PptxParser().parse(data)
        md   = doc.render(front_matter=True)
        assert "---" in md
        assert "pptx" in md

    def test_no_front_matter_when_suppressed(self):
        data = _make_pptx()
        doc  = PptxParser().parse(data)
        md   = doc.render(front_matter=False)
        assert not md.startswith("---")

    def test_slide_heading_in_markdown(self):
        data = _make_pptx(slides=[{"title": "Executive Summary"}])
        doc  = PptxParser().parse(data)
        md   = doc.render(front_matter=False)
        assert "Executive Summary" in md

    def test_table_pipe_syntax_in_output(self):
        data = _make_pptx(slides=[{
            "table": [["A", "B"], ["1", "2"]],
        }])
        doc = PptxParser().parse(data)
        md  = doc.render(front_matter=False)
        assert "|" in md

    def test_blockquote_in_markdown(self):
        data = _make_pptx(slides=[{
            "title": "T",
            "notes": "Speaker note text.",
        }])
        doc = PptxParser().parse(data)
        md  = doc.render(front_matter=False)
        assert ">" in md  # blockquote marker
        assert "Speaker note text." in md
