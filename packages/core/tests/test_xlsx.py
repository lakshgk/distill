"""
Tests for distill.parsers.xlsx — XlsxParser, XlsLegacyParser, and helpers.

Fixtures are built programmatically via openpyxl so no binary files are
checked into the repository.
"""

from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from pathlib import Path

import pytest

from distill.ir import Document, Section, Table
from distill.parsers.base import ParseError
from distill.parsers.xlsx import (
    XlsLegacyParser,
    XlsxParser,
    _check_input_size,
    _check_zip_bomb,
    _expand_merged_cells,
    _rightmost_non_empty,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _make_xlsx(
    *,
    sheets: dict[str, list[list]] | None = None,
    title: str = "",
    author: str = "",
    subject: str = "",
    description: str = "",
    keywords: str = "",
) -> bytes:
    """Build a minimal .xlsx in memory and return its raw bytes."""
    import openpyxl

    wb = openpyxl.Workbook()
    first = True
    for sheet_name, rows in (sheets or {"Sheet1": [["A", "B"], [1, 2]]}).items():
        if first:
            ws = wb.active
            ws.title = sheet_name
            first = False
        else:
            ws = wb.create_sheet(sheet_name)
        for row in rows:
            ws.append(row)

    wb.properties.title       = title
    wb.properties.creator     = author
    wb.properties.subject     = subject
    wb.properties.description = description
    wb.properties.keywords    = keywords

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_xlsx_with_merged(merge_range: str = "A1:B1") -> bytes:
    """Build a .xlsx where the given cell range is merged."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Merged"
    ws.append(["Category", "", "Value"])
    ws.merge_cells(merge_range)
    ws.append(["Alpha", "Sub", 10])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_xlsx_with_trailing_empty_cols() -> bytes:
    """Build a .xlsx with trailing empty columns on every row."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Trim"
    ws.append(["Name", "Score", None, None, None])
    ws.append(["Alice", 95, None, None, None])
    ws.append(["Bob", 88, None, None, None])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_csv(rows: list[list], tmp_path: Path) -> Path:
    p = tmp_path / "data.csv"
    with open(p, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    return p


def _cell_texts(table: Table) -> list[str]:
    return [
        run.text
        for row in table.rows
        for cell in row.cells
        for run in cell.content
    ]


def _tables(doc: Document) -> list[Table]:
    return [
        block
        for section in doc.sections
        for block in section.blocks
        if isinstance(block, Table)
    ]


# ── Parser availability ───────────────────────────────────────────────────────

class TestParserAvailability:
    def test_xlsx_is_available(self):
        assert XlsxParser.is_available()

    def test_xlsx_extensions(self):
        assert ".xlsx" in XlsxParser.extensions
        assert ".csv" in XlsxParser.extensions

    def test_xlsx_missing_requires_empty(self):
        assert XlsxParser.missing_requires() == []

    def test_xls_legacy_is_available(self):
        assert XlsLegacyParser.is_available()

    def test_xls_legacy_extension(self):
        assert ".xls" in XlsLegacyParser.extensions


# ── Basic parsing ─────────────────────────────────────────────────────────────

class TestBasicParsing:
    def test_returns_document(self):
        data   = _make_xlsx()
        result = XlsxParser().parse(data)
        assert isinstance(result, Document)

    def test_one_section_per_sheet(self):
        data = _make_xlsx(sheets={
            "Alpha": [["X"], [1]],
            "Beta":  [["Y"], [2]],
        })
        doc = XlsxParser().parse(data)
        headings = [r.text for s in doc.sections for r in (s.heading or [])]
        assert "Alpha" in headings
        assert "Beta"  in headings

    def test_section_level_is_2(self):
        data = _make_xlsx()
        doc  = XlsxParser().parse(data)
        assert all(s.level == 2 for s in doc.sections)

    def test_table_in_section(self):
        data = _make_xlsx(sheets={"Data": [["Col1", "Col2"], ["a", "b"]]})
        doc  = XlsxParser().parse(data)
        tbls = _tables(doc)
        assert len(tbls) == 1

    def test_first_row_is_header(self):
        data = _make_xlsx(sheets={"S": [["Name", "Age"], ["Alice", 30]]})
        doc  = XlsxParser().parse(data)
        header_row = _tables(doc)[0].rows[0]
        assert all(c.is_header for c in header_row.cells)

    def test_data_rows_not_header(self):
        data = _make_xlsx(sheets={"S": [["Name", "Age"], ["Alice", 30]]})
        doc  = XlsxParser().parse(data)
        data_row = _tables(doc)[0].rows[1]
        assert all(not c.is_header for c in data_row.cells)

    def test_cell_values_extracted(self):
        data = _make_xlsx(sheets={"S": [["Region", "Sales"], ["North", 500]]})
        doc  = XlsxParser().parse(data)
        texts = _cell_texts(_tables(doc)[0])
        assert "Region" in texts
        assert "North"  in texts
        assert "500"    in texts

    def test_accepts_path(self, tmp_path):
        p = tmp_path / "test.xlsx"
        p.write_bytes(_make_xlsx())
        doc = XlsxParser().parse(str(p))
        assert isinstance(doc, Document)

    def test_accepts_path_object(self, tmp_path):
        p = tmp_path / "test.xlsx"
        p.write_bytes(_make_xlsx())
        doc = XlsxParser().parse(p)
        assert isinstance(doc, Document)

    def test_multiple_sheets_all_extracted(self):
        data = _make_xlsx(sheets={
            "Sheet1": [["A"], [1]],
            "Sheet2": [["B"], [2]],
            "Sheet3": [["C"], [3]],
        })
        doc = XlsxParser().parse(data)
        assert len(doc.sections) == 3


# ── Metadata ──────────────────────────────────────────────────────────────────

class TestMetadata:
    def test_source_format(self):
        doc = XlsxParser().parse(_make_xlsx())
        assert doc.metadata.source_format == "xlsx"

    def test_sheet_count(self):
        data = _make_xlsx(sheets={"A": [["x"], [1]], "B": [["y"], [2]]})
        doc  = XlsxParser().parse(data)
        assert doc.metadata.sheet_count == 2

    def test_title(self, tmp_path):
        p = tmp_path / "t.xlsx"
        p.write_bytes(_make_xlsx(title="My Workbook"))
        doc = XlsxParser().parse(p)
        assert doc.metadata.title == "My Workbook"

    def test_author(self, tmp_path):
        p = tmp_path / "t.xlsx"
        p.write_bytes(_make_xlsx(author="Jane Doe"))
        doc = XlsxParser().parse(p)
        assert doc.metadata.author == "Jane Doe"

    def test_subject(self, tmp_path):
        p = tmp_path / "t.xlsx"
        p.write_bytes(_make_xlsx(subject="Q4 Finance"))
        doc = XlsxParser().parse(p)
        assert doc.metadata.subject == "Q4 Finance"

    def test_description(self, tmp_path):
        p = tmp_path / "t.xlsx"
        p.write_bytes(_make_xlsx(description="Annual report data"))
        doc = XlsxParser().parse(p)
        assert doc.metadata.description == "Annual report data"

    def test_keywords_comma(self, tmp_path):
        p = tmp_path / "t.xlsx"
        p.write_bytes(_make_xlsx(keywords="finance, q4, 2025"))
        doc = XlsxParser().parse(p)
        assert "finance" in doc.metadata.keywords
        assert "q4"      in doc.metadata.keywords

    def test_keywords_semicolon(self, tmp_path):
        p = tmp_path / "t.xlsx"
        p.write_bytes(_make_xlsx(keywords="alpha;beta;gamma"))
        doc = XlsxParser().parse(p)
        assert doc.metadata.keywords == ["alpha", "beta", "gamma"]


# ── Merged cells ──────────────────────────────────────────────────────────────

class TestMergedCells:
    def test_merged_value_repeated(self):
        data = _make_xlsx_with_merged("A1:B1")
        doc  = XlsxParser().parse(data)
        tbls = _tables(doc)
        assert len(tbls) == 1
        header_texts = [run.text for c in tbls[0].rows[0].cells for run in c.content]
        assert header_texts.count("Category") == 2

    def test_helper_expand_merged_cells(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Merged", None, "C"])
        ws.merge_cells("A1:B1")
        result = _expand_merged_cells(ws)
        assert result[(1, 1)] == "Merged"
        assert result[(1, 2)] == "Merged"
        assert (1, 3) not in result


# ── Empty column trimming ─────────────────────────────────────────────────────

class TestEmptyColumnTrim:
    def test_trailing_empty_cols_removed(self):
        data = _make_xlsx_with_trailing_empty_cols()
        doc  = XlsxParser().parse(data)
        tbls = _tables(doc)
        assert len(tbls) == 1
        col_count = len(tbls[0].rows[0].cells)
        assert col_count == 2

    def test_rightmost_non_empty_helper(self):
        rows = [["A", "B", "", ""], ["1", "2", "", ""]]
        assert _rightmost_non_empty(rows) == 2

    def test_rightmost_non_empty_all_empty(self):
        rows = [["", ""], ["", ""]]
        assert _rightmost_non_empty(rows) == 0

    def test_rightmost_non_empty_last_col_populated(self):
        rows = [["", "", "C"], ["", "", "3"]]
        assert _rightmost_non_empty(rows) == 3


# ── Row truncation ────────────────────────────────────────────────────────────

class TestRowTruncation:
    def test_truncation_applied(self):
        from distill.parsers.base import ParseOptions
        rows = [["H"]] + [[str(i)] for i in range(200)]
        data = _make_xlsx(sheets={"Big": rows})
        opts = ParseOptions(max_table_rows=10)
        doc  = XlsxParser().parse(data, options=opts)
        tbls = _tables(doc)
        assert len(tbls[0].rows) == 10

    def test_truncation_warning_emitted(self):
        from distill.parsers.base import ParseOptions
        rows = [["H"]] + [[str(i)] for i in range(200)]
        data = _make_xlsx(sheets={"Big": rows})
        opts = ParseOptions(max_table_rows=10)
        doc  = XlsxParser().parse(data, options=opts)
        assert any("truncated" in w for w in doc.warnings)

    def test_no_truncation_when_unlimited(self):
        from distill.parsers.base import ParseOptions
        rows = [["H"]] + [[str(i)] for i in range(600)]
        data = _make_xlsx(sheets={"Big": rows})
        opts = ParseOptions(max_table_rows=0)
        doc  = XlsxParser().parse(data, options=opts)
        tbls = _tables(doc)
        assert len(tbls[0].rows) == 601


# ── CSV path ──────────────────────────────────────────────────────────────────

class TestCsvParsing:
    def test_csv_returns_document(self, tmp_path):
        p   = _make_csv([["Name", "Age"], ["Alice", "30"]], tmp_path)
        doc = XlsxParser().parse(p)
        assert isinstance(doc, Document)

    def test_csv_source_format(self, tmp_path):
        p   = _make_csv([["X"], ["1"]], tmp_path)
        doc = XlsxParser().parse(p)
        assert doc.metadata.source_format == "csv"

    def test_csv_values_extracted(self, tmp_path):
        p    = _make_csv([["City", "Pop"], ["Oslo", "700000"]], tmp_path)
        doc  = XlsxParser().parse(p)
        tbls = _tables(doc)
        texts = _cell_texts(tbls[0])
        assert "City"   in texts
        assert "Oslo"   in texts
        assert "700000" in texts

    def test_csv_first_row_header(self, tmp_path):
        p   = _make_csv([["H1", "H2"], ["v1", "v2"]], tmp_path)
        doc = XlsxParser().parse(p)
        tbls = _tables(doc)
        assert all(c.is_header for c in tbls[0].rows[0].cells)

    def test_csv_trailing_empty_cols_trimmed(self, tmp_path):
        p   = _make_csv([["A", "B", "", ""], ["1", "2", "", ""]], tmp_path)
        doc = XlsxParser().parse(p)
        tbls = _tables(doc)
        assert len(tbls[0].rows[0].cells) == 2


# ── Security ──────────────────────────────────────────────────────────────────

class TestSecurity:
    def test_input_size_bytes_rejected(self):
        oversized = b"x" * (55 * 1024 * 1024)
        with pytest.raises(ParseError, match="50 MB"):
            _check_input_size(oversized, 50 * 1024 * 1024)

    def test_input_size_path_rejected(self, tmp_path):
        big = tmp_path / "big.xlsx"
        big.write_bytes(b"x" * (55 * 1024 * 1024))
        with pytest.raises(ParseError, match="50 MB"):
            _check_input_size(str(big), 50 * 1024 * 1024)

    def test_input_size_ok(self):
        _check_input_size(b"x" * 100, 50 * 1024 * 1024)  # must not raise

    def test_zip_bomb_detected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.bin", "A" * (501 * 1024 * 1024))
        with pytest.raises(ParseError, match="500 MB"):
            _check_zip_bomb(buf.getvalue(), 500 * 1024 * 1024)

    def test_bad_zip_raises(self):
        with pytest.raises(ParseError, match="not a valid XLSX"):
            _check_zip_bomb(b"not a zip file", 500 * 1024 * 1024)

    def test_parser_rejects_oversized(self):
        oversized = b"x" * (55 * 1024 * 1024)
        with pytest.raises(ParseError, match="50 MB"):
            XlsxParser().parse(oversized)

    def test_custom_size_limit(self):
        from distill.parsers.base import ParseOptions
        data = b"x" * (15 * 1024 * 1024)
        opts = ParseOptions(extra={"max_file_size": 10 * 1024 * 1024})
        with pytest.raises(ParseError, match="10 MB"):
            XlsxParser().parse(data, options=opts)


# ── XlsLegacyParser ──────────────────────────────────────────────────────────

class TestXlsLegacyParser:
    def test_raises_parse_error(self):
        # Wired to LibreOffice — will fail due to binary not found or conversion error
        with pytest.raises(ParseError):
            XlsLegacyParser().parse(b"garbage")

    def test_error_mentions_libreoffice(self):
        with pytest.raises(ParseError, match="LibreOffice"):
            XlsLegacyParser().parse(b"garbage")

    def test_extension_registered(self):
        assert ".xls" in XlsLegacyParser.extensions


# ── Render integration ─────────────────────────────────────────────────────────

class TestRenderIntegration:
    def test_renders_to_markdown(self):
        data = _make_xlsx(sheets={"Data": [["Item", "Qty"], ["Apples", 5]]})
        doc  = XlsxParser().parse(data)
        md   = doc.render(front_matter=False)
        assert isinstance(md, str)
        assert "Item" in md or "Apples" in md

    def test_front_matter_has_source_format(self):
        data = _make_xlsx()
        doc  = XlsxParser().parse(data)
        md   = doc.render(front_matter=True)
        assert "---" in md
        assert "xlsx" in md

    def test_no_front_matter_when_suppressed(self):
        data = _make_xlsx()
        doc  = XlsxParser().parse(data)
        md   = doc.render(front_matter=False)
        # The YAML front-matter block starts with "---\n"; GFM table separators
        # also contain "---" but not at position 0 of the output.
        assert not md.startswith("---")

    def test_sheet_name_as_heading(self):
        data = _make_xlsx(sheets={"MySales": [["A"], [1]]})
        doc  = XlsxParser().parse(data)
        md   = doc.render(front_matter=False)
        assert "MySales" in md

    def test_table_pipe_syntax_in_output(self):
        data = _make_xlsx(sheets={"T": [["Col1", "Col2"], ["r1", "r2"]]})
        doc  = XlsxParser().parse(data)
        md   = doc.render(front_matter=False)
        assert "|" in md
