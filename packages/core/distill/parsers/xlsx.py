"""
distill.parsers.xlsx
~~~~~~~~~~~~~~~~~~~~
Parser for Microsoft Excel workbooks (.xlsx, .xls, .csv).

Primary path:   openpyxl (.xlsx structural read) + pandas/tabulate (table render)
Lightweight:    csv module (for plain .csv files)
Legacy path:    LibreOffice headless (.xls → .xlsx pre-conversion)

Key design decisions:
- Each worksheet becomes a separate H2 Section
- Formulas: render computed value, not formula text
- Merged cells: unmerge and repeat header value
- Row cap: configurable via ParseOptions.max_table_rows (default 500)
- Empty/chart-only sheets: skipped with a warning

Install:
    pip install distill-core          # includes openpyxl, pandas, tabulate
    pip install distill-core[legacy]  # adds .xls support via LibreOffice
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    Document, DocumentMetadata, Paragraph, Section, Table,
    TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser
from distill.registry import registry


@registry.register
class XlsxParser(Parser):
    """Parses .xlsx workbooks using openpyxl."""

    extensions = [".xlsx", ".csv"]
    mime_types = [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    ]
    requires          = ["openpyxl"]
    optional_requires = ["pandas", "tabulate"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()
        path    = Path(source) if not isinstance(source, bytes) else None

        if path and path.suffix.lower() == ".csv":
            return self._parse_csv(path, options)

        return self._parse_xlsx(path, source, options)

    def _parse_xlsx(self, path, source, options: ParseOptions) -> Document:
        try:
            import openpyxl
        except ImportError as e:
            raise ParseError(f"Missing dependency: {e}") from e

        try:
            if path:
                wb = openpyxl.load_workbook(str(path), data_only=True)
            else:
                import io
                wb = openpyxl.load_workbook(io.BytesIO(source), data_only=True)
        except Exception as e:
            raise ParseError(f"openpyxl failed to open workbook: {e}") from e

        metadata = DocumentMetadata(
            source_format = "xlsx",
            source_path   = str(path) if path else None,
            sheet_count   = len(wb.sheetnames),
        )
        document = Document(metadata=metadata)

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]

            # Skip empty or chart-only sheets
            if ws.max_row is None or ws.max_row == 0:
                document.warnings.append(f"[xlsx] Skipped empty sheet: {sheet_name!r}")
                continue

            section = Section(
                heading = [TextRun(text=sheet_name)],
                level   = 2,
            )

            table = self._worksheet_to_table(ws, options, sheet_name, document)
            if table:
                section.blocks.append(table)

            document.sections.append(section)

        return document

    def _worksheet_to_table(self, ws, options: ParseOptions, sheet_name: str, doc: Document):
        rows_data = []
        for row in ws.iter_rows(values_only=True):
            rows_data.append([str(cell) if cell is not None else "" for cell in row])

        if not rows_data:
            return None

        total_rows = len(rows_data)
        truncated  = False

        if options.max_table_rows > 0 and total_rows > options.max_table_rows:
            rows_data = rows_data[:options.max_table_rows]
            truncated = True
            doc.warnings.append(
                f"[xlsx] Sheet {sheet_name!r}: truncated to {options.max_table_rows} "
                f"of {total_rows} rows"
            )

        def make_row(cells: list[str], is_header=False) -> TableRow:
            return TableRow(cells=[
                TableCell(
                    content   = [TextRun(text=cell)],
                    is_header = is_header,
                )
                for cell in cells
            ])

        table_rows = [make_row(rows_data[0], is_header=True)]
        for row in rows_data[1:]:
            table_rows.append(make_row(row))

        return Table(
            rows       = table_rows,
            truncated  = truncated,
            total_rows = total_rows if truncated else None,
        )

    def _parse_csv(self, path: Path, options: ParseOptions) -> Document:
        import csv
        metadata = DocumentMetadata(source_format="csv", source_path=str(path))
        document = Document(metadata=metadata)

        with open(path, newline="", encoding="utf-8-sig") as f:
            reader    = csv.reader(f)
            rows_data = list(reader)

        if not rows_data:
            return document

        total_rows = len(rows_data)
        truncated  = False
        if options.max_table_rows > 0 and total_rows > options.max_table_rows:
            rows_data = rows_data[:options.max_table_rows]
            truncated = True

        def make_row(cells, is_header=False):
            return TableRow(cells=[
                TableCell(content=[TextRun(text=c)], is_header=is_header)
                for c in cells
            ])

        table_rows = [make_row(rows_data[0], is_header=True)]
        for row in rows_data[1:]:
            table_rows.append(make_row(row))

        table   = Table(rows=table_rows, truncated=truncated, total_rows=total_rows if truncated else None)
        section = Section(level=0, blocks=[table])
        document.sections.append(section)
        return document
