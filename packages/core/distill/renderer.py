"""
distill.renderer
~~~~~~~~~~~~~~~~
Renders an IR Document tree to CommonMark / GFM Markdown.
"""

from __future__ import annotations

from typing import Iterator, Optional

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
        Emit a YAML front-matter block with document metadata (default: False).
        Set to True to include metadata in the output.
    max_heading_depth : int
        Cap heading levels at this depth (default: 6)
    table_alignment : bool
        Emit column alignment markers in GFM tables (default: True)
    image_mode : str
        How to render images — see Image node priority rules (default: "auto")
    """

    def __init__(
        self,
        front_matter:      bool = False,
        max_heading_depth: int  = 6,
        table_alignment:   bool = True,
        image_mode:        str  = "auto",
        paginate_output:   bool = False,
    ):
        self.front_matter      = front_matter
        self.max_heading_depth = max_heading_depth
        self.table_alignment   = table_alignment
        self.image_mode        = image_mode
        self.paginate_output   = paginate_output
        self._last_page: int | None = None   # tracks the last emitted page number

    # ── Entry point ──────────────────────────────────────────────────────────

    def render(self, doc: Document) -> str:
        return "\n\n".join(self.render_stream(doc))

    def render_stream(self, doc: Document) -> Iterator[str]:
        """Yield rendered Markdown chunks one section at a time.

        If front_matter is enabled, the first yielded chunk is the YAML block.
        Each subsequent chunk is one top-level section rendered to Markdown.
        Empty sections are skipped.
        """
        self._last_page = None  # reset per render call

        if self.front_matter:
            fm = self._front_matter(doc)
            if fm:
                yield fm

        for section in doc.sections:
            rendered = self._render_section(section)
            if rendered.strip():
                yield rendered

    # ── Front matter ─────────────────────────────────────────────────────────

    def _front_matter(self, doc: Document) -> str:
        m = doc.metadata
        lines = ["---"]
        if m.title:
            lines.append(f"title: {m.title!r}")
        if m.author:
            lines.append(f"author: {m.author!r}")
        if m.subject:
            lines.append(f"subject: {m.subject!r}")
        if m.description:
            lines.append(f"description: {m.description!r}")
        if m.keywords:
            kw = ", ".join(f"{k!r}" for k in m.keywords)
            lines.append(f"keywords: [{kw}]")
        if m.created_at:
            lines.append(f"created: {m.created_at}")
        if m.modified_at:
            lines.append(f"modified: {m.modified_at}")
        if m.source_format:
            lines.append(f"source_format: {m.source_format}")
        if m.page_count is not None:
            lines.append(f"page_count: {m.page_count}")
        if m.slide_count is not None:
            lines.append(f"slide_count: {m.slide_count}")
        if m.sheet_count is not None:
            lines.append(f"sheet_count: {m.sheet_count}")
        if m.word_count is not None:
            lines.append(f"word_count: {m.word_count}")
        lines.append("---")
        return "\n".join(lines) if len(lines) > 2 else ""

    # ── Sections ─────────────────────────────────────────────────────────────

    def _render_section(self, section: Section, depth: int = 0) -> str:
        parts: list[str] = []
        level = min(section.level, self.max_heading_depth)

        # Page separator — only when paginate_output=True and the section carries
        # a `page` attribute (not yet in the IR schema; this is a no-op until added).
        if self.paginate_output:
            try:
                page = getattr(section, "page", None)
                if page is not None and page != self._last_page:
                    if self._last_page is not None:
                        parts.append(f"---\n*Page {page}*")
                    self._last_page = page
            except Exception:
                pass  # never raise; missing page metadata is silently ignored

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

    def _render_table(self, table: Table, options=None) -> str:
        if not table.rows:
            return ""

        if getattr(table, "merged_cells", False) or getattr(table, "complex_headers", False):
            return self._render_table_html(table, options)

        # GFM pipe table
        lines: list[str] = []
        if table.caption:
            lines.append(f"*{table.caption}*\n")

        if table.truncated and table.total_rows:
            lines.append(
                f"<!-- Table truncated: showing first rows of {table.total_rows} total -->"
            )

        # Collect all rows as string grids — strip newlines from cells
        grid: list[list[str]] = []
        for row in table.rows:
            grid.append([
                self._render_cell(cell).replace("\n", " ").strip()
                for cell in row.cells
            ])

        if not grid:
            return ""

        col_count = max(len(r) for r in grid)

        # Pad short rows
        for r in grid:
            while len(r) < col_count:
                r.append("")

        # Header row (first row)
        lines.append("| " + " | ".join(grid[0]) + " |")

        # Alignment separator — no alignment hints
        lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")

        # Body rows
        for r in grid[1:]:
            lines.append("| " + " | ".join(r) + " |")

        return "\n".join(lines)

    def _render_table_html(self, table: Table, options=None) -> str:
        """Render a complex table as semantic HTML and emit a warning."""
        import html as _html
        from distill.warnings import ConversionWarning, WarningType

        if options is not None and getattr(options, "collector", None) is not None:
            options.collector.add(ConversionWarning(
                type=WarningType.table_complex,
                message="Table contains merged cells or complex headers; rendered as HTML.",
                pages=getattr(table, "pages", None),
            ))

        first_row = table.rows[0]
        has_header = all(getattr(cell, "is_header", False) for cell in first_row.cells)

        parts = ["<table>"]
        start_idx = 0
        if has_header:
            parts.append("<thead><tr>")
            for cell in first_row.cells:
                parts.append(f"<th>{_html.escape(self._render_cell(cell))}</th>")
            parts.append("</tr></thead>")
            start_idx = 1

        parts.append("<tbody>")
        for row in table.rows[start_idx:]:
            parts.append("<tr>")
            for cell in row.cells:
                parts.append(f"<td>{_html.escape(self._render_cell(cell))}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")
        parts.append("</table>")

        return "".join(parts)

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

        # Alt field priority: caption > ocr_text (first 80 chars) > alt_text > ""
        caption = getattr(img, "caption", None)
        ocr_text = getattr(img, "ocr_text", None)
        alt_text = getattr(img, "alt_text", None)
        path = getattr(img, "path", None)

        if caption:
            alt_field = caption
        elif ocr_text:
            alt_field = ocr_text[:80]
        elif alt_text:
            alt_field = alt_text
        else:
            alt_field = ""

        src_field = path if path else ""

        # Suppress entirely if both empty
        if not alt_field and not src_field:
            return ""

        result = f"![{alt_field}]({src_field})"

        # Append OCR text as fenced code block
        if ocr_text:
            result += f"\n\n```\n{ocr_text}\n```"

        return result
