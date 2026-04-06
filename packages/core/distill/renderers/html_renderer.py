"""
distill.renderers.html_renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Renders an IR Document to clean semantic HTML.

- Section headings map to <h1>–<h6>
- Paragraph → <p>
- Table → <table> with <thead> / <tbody>
- List → <ul> / <ol>
- CodeBlock → <pre><code>
- BlockQuote → <blockquote>
- Image → <img> or omitted if decorative

No inline styles are emitted.
All IR node field accesses are null-guarded.
"""

from __future__ import annotations

import html as _html

from distill.ir import (
    Block, BlockQuote, CodeBlock, Document, Image, ImageType,
    List, ListItem, Paragraph, Section, Table, TableCell, TextRun,
)


class HTMLRenderer:
    """Converts an IR Document to a semantic HTML string."""

    def render(self, doc: Document) -> str:
        parts: list[str] = []
        for section in (doc.sections or []):
            parts.append(self._render_section(section))
        return "\n".join(p for p in parts if p.strip())

    # ── Sections ──────────────────────────────────────────────────────────────

    def _render_section(self, section: Section) -> str:
        parts: list[str] = []
        level = max(1, min(section.level or 1, 6))

        if section.heading:
            text = _html.escape(self._runs_to_text(section.heading))
            parts.append(f"<h{level}>{text}</h{level}>")

        for block in (section.blocks or []):
            rendered = self._render_block(block)
            if rendered:
                parts.append(rendered)

        for sub in (section.subsections or []):
            rendered = self._render_section(sub)
            if rendered:
                parts.append(rendered)

        return "\n".join(parts)

    # ── Blocks ────────────────────────────────────────────────────────────────

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
        inner = self._render_runs(p.runs or [])
        return f"<p>{inner}</p>" if inner.strip() else ""

    def _render_code_block(self, block: CodeBlock) -> str:
        code = _html.escape(block.code or "")
        return f"<pre><code>{code}</code></pre>"

    def _render_blockquote(self, bq: BlockQuote) -> str:
        inner = "\n".join(
            self._render_block(b) for b in (bq.content or [])
        )
        return f"<blockquote>\n{inner}\n</blockquote>" if inner.strip() else ""

    def _render_image(self, img: Image) -> str:
        if getattr(img, "image_type", None) == ImageType.DECORATIVE:
            return ""
        if getattr(img, "ocr_text", None):
            return f"<pre><code>{_html.escape(img.ocr_text.strip())}</code></pre>"
        alt  = _html.escape(img.caption or img.alt_text or "")
        path = _html.escape(img.path or "")
        if path:
            return f'<img src="{path}" alt="{alt}">'
        if alt:
            return f"<p>{alt}</p>"
        return ""

    # ── Tables ────────────────────────────────────────────────────────────────

    def _render_table(self, table: Table) -> str:
        rows = table.rows or []
        if not rows:
            return ""

        lines: list[str] = ["<table>"]

        # First row → <thead>
        header_cells = rows[0].cells or []
        lines.append("<thead><tr>")
        for cell in header_cells:
            lines.append(f"<th>{self._render_cell(cell)}</th>")
        lines.append("</tr></thead>")

        # Remaining rows → <tbody>
        if len(rows) > 1:
            lines.append("<tbody>")
            for row in rows[1:]:
                lines.append("<tr>")
                for cell in (row.cells or []):
                    lines.append(f"<td>{self._render_cell(cell)}</td>")
                lines.append("</tr>")
            lines.append("</tbody>")

        lines.append("</table>")
        return "\n".join(lines)

    def _render_cell(self, cell: TableCell) -> str:
        parts = []
        for item in (cell.content or []):
            if isinstance(item, TextRun):
                parts.append(self._render_runs([item]))
            elif isinstance(item, Paragraph):
                parts.append(self._render_runs(item.runs or []))
        return " ".join(parts)

    # ── Lists ─────────────────────────────────────────────────────────────────

    def _render_list(self, lst: List) -> str:
        tag = "ol" if lst.ordered else "ul"
        items = "\n".join(
            self._render_list_item(it, lst.ordered)
            for it in (lst.items or [])
        )
        return f"<{tag}>\n{items}\n</{tag}>"

    def _render_list_item(self, item: ListItem, ordered: bool) -> str:
        text = self._render_runs(item.content or [])
        children = "".join(
            self._render_list(child) for child in (item.children or [])
        )
        return f"<li>{text}{children}</li>"

    # ── Inline ────────────────────────────────────────────────────────────────

    def _render_runs(self, runs: list[TextRun]) -> str:
        parts = []
        for run in runs:
            text = _html.escape(run.text or "")
            if getattr(run, "code", False):
                text = f"<code>{text}</code>"
            else:
                if run.bold and run.italic:
                    text = f"<strong><em>{text}</em></strong>"
                elif run.bold:
                    text = f"<strong>{text}</strong>"
                elif run.italic:
                    text = f"<em>{text}</em>"
                if getattr(run, "strikethrough", False):
                    text = f"<s>{text}</s>"
            if getattr(run, "href", None):
                href = _html.escape(run.href)
                text = f'<a href="{href}">{text}</a>'
            parts.append(text)
        return "".join(parts)

    def _runs_to_text(self, runs: list[TextRun]) -> str:
        return "".join(run.text or "" for run in (runs or []))
