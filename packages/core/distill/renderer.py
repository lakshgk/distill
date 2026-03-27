"""
distill.renderer
~~~~~~~~~~~~~~~~
Renders an IR Document tree to CommonMark / GFM Markdown.
"""

from __future__ import annotations

from typing import Optional

from distill.ir import (
    Alignment, Block, BlockQuote, CodeBlock, Document, Image, ImageType,
    List, ListItem, Paragraph, Section, Table, TableCell, TextRun,
)


class MarkdownRenderer:
    """
    Converts an IR Document to a Markdown string.

    Options
    -------
    front_matter : bool
        Emit a YAML front-matter block with document metadata (default: True)
    max_heading_depth : int
        Cap heading levels at this depth (default: 6)
    table_alignment : bool
        Emit column alignment markers in GFM tables (default: True)
    image_mode : str
        How to render images — see Image node priority rules (default: "auto")
    """

    def __init__(
        self,
        front_matter:      bool = True,
        max_heading_depth: int  = 6,
        table_alignment:   bool = True,
        image_mode:        str  = "auto",
    ):
        self.front_matter      = front_matter
        self.max_heading_depth = max_heading_depth
        self.table_alignment   = table_alignment
        self.image_mode        = image_mode

    # ── Entry point ──────────────────────────────────────────────────────────

    def render(self, doc: Document) -> str:
        parts: list[str] = []

        if self.front_matter:
            fm = self._front_matter(doc)
            if fm:
                parts.append(fm)

        for section in doc.sections:
            parts.append(self._render_section(section))

        return "\n\n".join(p for p in parts if p.strip())

    # ── Front matter ─────────────────────────────────────────────────────────

    def _front_matter(self, doc: Document) -> str:
        m = doc.metadata
        lines = ["---"]
        if m.title:
            lines.append(f"title: {m.title!r}")
        if m.author:
            lines.append(f"author: {m.author!r}")
        if m.created_at:
            lines.append(f"date: {m.created_at}")
        if m.source_format:
            lines.append(f"source_format: {m.source_format}")
        if m.page_count is not None:
            lines.append(f"page_count: {m.page_count}")
        if m.word_count is not None:
            lines.append(f"word_count: {m.word_count}")
        lines.append("---")
        return "\n".join(lines) if len(lines) > 2 else ""

    # ── Sections ─────────────────────────────────────────────────────────────

    def _render_section(self, section: Section, depth: int = 0) -> str:
        parts: list[str] = []
        level = min(section.level, self.max_heading_depth)

        if section.heading:
            heading_text = self._render_runs(section.heading)
            parts.append(f"{'#' * level} {heading_text}")

        for block in section.blocks:
            rendered = self._render_block(block)
            if rendered:
                parts.append(rendered)

        for sub in section.subsections:
            parts.append(self._render_section(sub, depth + 1))

        return "\n\n".join(p for p in parts if p.strip())

    # ── Blocks ───────────────────────────────────────────────────────────────

    def _render_block(self, block: Block) -> str:
        if isinstance(block, Paragraph):
            return self._render_paragraph(block)
        if isinstance(block, Table):
            return self._render_table(block)
        if isinstance(block, List):
            return self._render_list(block)
        if isinstance(block, CodeBlock):
            return self._render_code_block(block)
        if isinstance(block, BlockQuote):
            return self._render_blockquote(block)
        if isinstance(block, Image):
            return self._render_image(block)
        return ""

    def _render_paragraph(self, p: Paragraph) -> str:
        return self._render_runs(p.runs)

    def _render_runs(self, runs: list[TextRun]) -> str:
        parts = []
        for run in runs:
            text = run.text.replace("|", "\\|")  # escape pipes inside tables
            if run.code:
                text = f"`{text}`"
            else:
                if run.bold and run.italic:
                    text = f"***{text}***"
                elif run.bold:
                    text = f"**{text}**"
                elif run.italic:
                    text = f"*{text}*"
                if run.strikethrough:
                    text = f"~~{text}~~"
            if run.href:
                text = f"[{text}]({run.href})"
            parts.append(text)
        return "".join(parts)

    # ── Tables ───────────────────────────────────────────────────────────────

    def _render_table(self, table: Table) -> str:
        if not table.rows:
            return ""

        lines = []
        if table.caption:
            lines.append(f"*{table.caption}*\n")

        if table.truncated and table.total_rows:
            lines.append(
                f"<!-- Table truncated: showing first rows of {table.total_rows} total -->"
            )

        # Collect all rows as string grids
        grid: list[list[str]] = []
        for row in table.rows:
            grid.append([self._render_cell(cell) for cell in row.cells])

        if not grid:
            return ""

        col_count = max(len(row) for row in grid)

        # Pad short rows
        for row in grid:
            while len(row) < col_count:
                row.append("")

        # Column widths
        widths = [max(len(grid[r][c]) for r in range(len(grid))) for c in range(col_count)]
        widths = [max(w, 3) for w in widths]

        def fmt_row(cells: list[str]) -> str:
            padded = [c.ljust(widths[i]) for i, c in enumerate(cells)]
            return "| " + " | ".join(padded) + " |"

        # Header row (first row)
        lines.append(fmt_row(grid[0]))

        # Separator
        sep_cells = ["-" * widths[i] for i in range(col_count)]
        lines.append("| " + " | ".join(sep_cells) + " |")

        # Body rows
        for row in grid[1:]:
            lines.append(fmt_row(row))

        return "\n".join(lines)

    def _render_cell(self, cell: TableCell) -> str:
        parts = []
        for item in cell.content:
            if isinstance(item, TextRun):
                parts.append(self._render_runs([item]))
            elif isinstance(item, Paragraph):
                parts.append(self._render_paragraph(item))
        return " ".join(parts)

    # ── Lists ────────────────────────────────────────────────────────────────

    def _render_list(self, lst: List, indent: int = 0) -> str:
        lines = []
        prefix = "  " * indent
        for i, item in enumerate(lst.items):
            marker = f"{i + 1}." if lst.ordered else "-"
            text   = self._render_runs(item.content)
            lines.append(f"{prefix}{marker} {text}")
            for child_list in item.children:
                lines.append(self._render_list(child_list, indent + 1))
        return "\n".join(lines)

    # ── Code blocks ──────────────────────────────────────────────────────────

    def _render_code_block(self, block: CodeBlock) -> str:
        lang = block.language or ""
        return f"```{lang}\n{block.code}\n```"

    # ── Blockquotes ──────────────────────────────────────────────────────────

    def _render_blockquote(self, bq: BlockQuote) -> str:
        inner = "\n\n".join(self._render_block(b) for b in bq.content)
        return "\n".join(f"> {line}" for line in inner.splitlines())

    # ── Images ───────────────────────────────────────────────────────────────

    def _render_image(self, img: Image) -> str:
        # Decorative images are always suppressed
        if img.image_type == ImageType.DECORATIVE:
            return ""

        # Priority: structured_data > ocr_text > caption > alt_text > path
        if img.structured_data:
            rows = img.structured_data
            if rows:
                headers = list(rows[0].keys())
                tbl_rows = [[str(r.get(h, "")) for h in headers] for r in rows]
                widths   = [max(len(h), max((len(r[i]) for r in tbl_rows), default=0))
                            for i, h in enumerate(headers)]
                lines    = []
                lines.append("| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |")
                lines.append("| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |")
                for row in tbl_rows:
                    lines.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(headers))) + " |")
                caption = f"\n*{img.alt_text or 'Chart data'}*" if img.alt_text else ""
                return "\n".join(lines) + caption

        if img.ocr_text:
            return f"```\n{img.ocr_text.strip()}\n```"

        alt  = img.caption or img.alt_text or ""
        path = img.path or ""
        if path or alt:
            return f"![{alt}]({path})"

        return ""
