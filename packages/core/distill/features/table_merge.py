"""
distill.features.table_merge
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Cross-page table fragment detection and LLM-powered merging.

Detection runs unconditionally on PDF input and emits CROSS_PAGE_TABLE
warnings.  Merging is opt-in via ``options.llm_merge_tables=True``.
"""

from __future__ import annotations

import logging
from typing import Optional

from distill.ir import Document, Section, Table
from distill.warnings import ConversionWarning, WarningCollector, WarningType

_logger = logging.getLogger(__name__)


# ── Detection ───────────────────────────────────────────────────────────────

class TableFragmentDetector:
    """Detect adjacent table sections that appear to span a page boundary."""

    def detect(
        self,
        doc: Document,
        collector: WarningCollector,
    ) -> list[tuple[int, int]]:
        """Return ``(section_a, section_b)`` index pairs for probable fragments.

        Detection heuristic:
        - Both sections' first block is a ``Table``.
        - They appear on consecutive pages (requires a ``page`` attribute on
          ``Table``; silently skipped if absent).
        - Column count matches exactly or differs by at most 1.
        - The second table's first row does not repeat the first table's
          header values (indicating it is a continuation, not a new table).

        For each detected pair a ``CROSS_PAGE_TABLE`` warning is emitted.
        Never raises — returns an empty list on any error.
        """
        try:
            return self._detect_impl(doc, collector)
        except Exception as exc:
            _logger.debug("TableFragmentDetector.detect error: %s", exc)
            return []

    # ── Internal ─────────────────────────────────────────────────────────────

    def _detect_impl(
        self,
        doc: Document,
        collector: WarningCollector,
    ) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []

        for i in range(len(doc.sections) - 1):
            tbl_a = self._first_table(doc.sections[i])
            tbl_b = self._first_table(doc.sections[i + 1])
            if tbl_a is None or tbl_b is None:
                continue

            # Page metadata check — skip if not present on both tables
            page_a = getattr(tbl_a, "page", None)
            page_b = getattr(tbl_b, "page", None)
            if page_a is None or page_b is None:
                continue

            # Must be consecutive pages
            if page_b != page_a + 1:
                continue

            # Column count must match (±1)
            cols_a = self._col_count(tbl_a)
            cols_b = self._col_count(tbl_b)
            if abs(cols_a - cols_b) > 1:
                continue

            # Second table should NOT repeat the first table's headers
            if self._has_matching_headers(tbl_a, tbl_b):
                continue

            pairs.append((i, i + 1))
            collector.add(ConversionWarning(
                type=WarningType.CROSS_PAGE_TABLE,
                message=(
                    f"Table on page {page_a} appears to continue onto page {page_b}."
                ),
                pages=[page_a, page_b],
            ))

        return pairs

    @staticmethod
    def _first_table(section: Section) -> Optional[Table]:
        for block in section.blocks:
            if isinstance(block, Table):
                return block
        return None

    @staticmethod
    def _col_count(table: Table) -> int:
        if table.rows:
            return len(table.rows[0].cells)
        return 0

    @staticmethod
    def _has_matching_headers(tbl_a: Table, tbl_b: Table) -> bool:
        """Return True if tbl_b's first row matches tbl_a's header row."""
        if not tbl_a.rows or not tbl_b.rows:
            return False
        header_a = tbl_a.rows[0]
        first_b = tbl_b.rows[0]
        if len(header_a.cells) != len(first_b.cells):
            return False

        def _cell_text(cell) -> str:
            parts = []
            for item in cell.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif hasattr(item, "runs"):
                    parts.extend(r.text for r in item.runs)
            return " ".join(parts).strip()

        return all(
            _cell_text(a) == _cell_text(b)
            for a, b in zip(header_a.cells, first_b.cells)
        )


# ── Merging ─────────────────────────────────────────────────────────────────

class TableMerger:
    """Merge cross-page table fragments using an LLM."""

    def __init__(self, client) -> None:
        self._client = client

    def merge(
        self,
        doc: Document,
        pairs: list[tuple[int, int]],
    ) -> Document:
        """Merge detected table fragment pairs in-place and return the Document.

        For each pair, the LLM is asked whether the two tables are
        continuations. If yes, the merged table replaces section A's table and
        section B is removed. If the LLM responds ``SEPARATE`` or any error
        occurs, both sections are left unchanged.

        All section index accesses are bounds-checked.
        """
        from distill.features.llm import LLMError
        from distill.renderer import MarkdownRenderer

        renderer = MarkdownRenderer()

        # Process pairs in reverse order so index removal doesn't invalidate
        # earlier indices.
        for idx_a, idx_b in sorted(pairs, reverse=True):
            if idx_a >= len(doc.sections) or idx_b >= len(doc.sections):
                continue

            tbl_a = self._first_table(doc.sections[idx_a])
            tbl_b = self._first_table(doc.sections[idx_b])
            if tbl_a is None or tbl_b is None:
                continue

            try:
                md_a = renderer._render_table(tbl_a)
                md_b = renderer._render_table(tbl_b)

                system = (
                    "You are a document structure analyst. You will be given two "
                    "GFM Markdown table fragments extracted from consecutive pages "
                    "of a PDF. Determine if they are continuations of the same table. "
                    "If yes, return a single merged GFM pipe table with the correct "
                    "headers on the first row and all data rows combined. "
                    "If no, return the single word SEPARATE and nothing else."
                )
                user = f"Table A:\n{md_a}\n\nTable B:\n{md_b}"

                response = self._client.complete(system, user)

                if response.strip().upper() == "SEPARATE":
                    continue

                merged_table = self._parse_markdown_table(response)
                if merged_table is None:
                    continue

                # Replace section A's table with the merged table
                for i, block in enumerate(doc.sections[idx_a].blocks):
                    if isinstance(block, Table):
                        doc.sections[idx_a].blocks[i] = merged_table
                        break

                # Remove section B
                del doc.sections[idx_b]

            except LLMError as exc:
                _logger.debug("TableMerger LLM call failed for pair (%d, %d): %s",
                              idx_a, idx_b, exc)
                continue
            except Exception as exc:
                _logger.debug("TableMerger error for pair (%d, %d): %s",
                              idx_a, idx_b, exc)
                continue

        return doc

    @staticmethod
    def _first_table(section: Section) -> Optional[Table]:
        for block in section.blocks:
            if isinstance(block, Table):
                return block
        return None

    @staticmethod
    def _parse_markdown_table(md: str) -> Optional[Table]:
        """Parse a GFM pipe table string back into an IR Table node."""
        from distill.ir import Paragraph, TableCell, TableRow, TextRun

        lines = [line.strip() for line in md.strip().splitlines()
                 if line.strip() and line.strip().startswith("|")]
        if len(lines) < 2:
            return None

        def parse_row(line: str, is_header: bool = False) -> Optional[TableRow]:
            # Split on | and strip outer empties
            parts = line.split("|")
            # Remove leading/trailing empty strings from split
            if parts and not parts[0].strip():
                parts = parts[1:]
            if parts and not parts[-1].strip():
                parts = parts[:-1]
            if not parts:
                return None
            cells = [
                TableCell(
                    content=[TextRun(text=p.strip())],
                    is_header=is_header,
                )
                for p in parts
            ]
            return TableRow(cells=cells)

        rows: list[TableRow] = []

        # First line is the header
        header = parse_row(lines[0], is_header=True)
        if header is None:
            return None
        rows.append(header)

        # Skip separator line (line with only dashes, pipes, colons, spaces)
        start = 1
        if start < len(lines) and all(c in "-|: " for c in lines[start]):
            start = 2

        for line in lines[start:]:
            row = parse_row(line)
            if row is not None:
                rows.append(row)

        return Table(rows=rows) if rows else None
