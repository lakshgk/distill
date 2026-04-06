"""
distill.renderers.json_renderer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Serialises an IR Document to a JSON-safe dict.

Output shape:
    {
        "title":         str | absent,
        "format":        str | absent,
        "nodes":         [...]
    }

Each node dict contains a "type" key and type-specific fields.
All optional IR fields are null-guarded; absent means omitted (not null).
"""

from __future__ import annotations

from distill.ir import (
    Block, BlockQuote, CodeBlock, Document, Image, List, ListItem,
    Paragraph, Section, Table, TableCell, TableRow, TextRun,
)


class JSONRenderer:
    """Converts an IR Document into a JSON-safe dict."""

    def render(self, doc: Document) -> dict:
        out: dict = {"nodes": []}

        if doc.metadata:
            meta = doc.metadata
            if getattr(meta, "title", None):
                out["title"] = meta.title
            if getattr(meta, "source_format", None):
                out["format"] = meta.source_format

        for section in (doc.sections or []):
            out["nodes"].extend(self._render_section(section))

        return out

    # ── Sections ──────────────────────────────────────────────────────────────

    def _render_section(self, section: Section) -> list[dict]:
        nodes: list[dict] = []

        if section.heading:
            node: dict = {
                "type":    "heading",
                "level":   section.level,
                "content": self._render_runs(section.heading),
            }
            nodes.append(node)

        for block in (section.blocks or []):
            result = self._render_block(block)
            if result is not None:
                nodes.append(result)

        for sub in (section.subsections or []):
            nodes.extend(self._render_section(sub))

        return nodes

    # ── Blocks ────────────────────────────────────────────────────────────────

    def _render_block(self, block: Block) -> dict | None:
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
        return None

    def _render_paragraph(self, p: Paragraph) -> dict:
        return {"type": "paragraph", "content": self._render_runs(p.runs or [])}

    def _render_code_block(self, block: CodeBlock) -> dict:
        node: dict = {"type": "code", "content": block.code or ""}
        if block.language:
            node["language"] = block.language
        return node

    def _render_blockquote(self, bq: BlockQuote) -> dict:
        children = []
        for b in (bq.content or []):
            result = self._render_block(b)
            if result is not None:
                children.append(result)
        return {"type": "blockquote", "nodes": children}

    def _render_image(self, img: Image) -> dict:
        node: dict = {"type": "image"}
        if getattr(img, "image_type", None):
            node["image_type"] = str(img.image_type.value) if hasattr(img.image_type, "value") else str(img.image_type)
        if getattr(img, "alt_text", None):
            node["alt_text"] = img.alt_text
        if getattr(img, "caption", None):
            node["caption"] = img.caption
        if getattr(img, "ocr_text", None):
            node["ocr_text"] = img.ocr_text
        if getattr(img, "path", None):
            node["path"] = img.path
        return node

    # ── Tables ────────────────────────────────────────────────────────────────

    def _render_table(self, table: Table) -> dict:
        node: dict = {"type": "table"}

        if getattr(table, "caption", None):
            node["caption"] = table.caption
        if getattr(table, "truncated", False):
            node["truncated"] = True
        if getattr(table, "total_rows", None) is not None:
            node["total_rows"] = table.total_rows

        rows = table.rows or []
        if not rows:
            node["headers"] = []
            node["rows"] = []
            return node

        # First row → headers; remaining rows → body
        header_row = rows[0]
        node["headers"] = [self._render_cell(c) for c in (header_row.cells or [])]
        node["rows"] = [
            [self._render_cell(c) for c in (row.cells or [])]
            for row in rows[1:]
        ]
        return node

    def _render_cell(self, cell: TableCell) -> str:
        parts = []
        for item in (cell.content or []):
            if isinstance(item, TextRun):
                parts.append(self._render_runs([item]))
            elif isinstance(item, Paragraph):
                parts.append(self._render_runs(item.runs or []))
        return " ".join(parts)

    # ── Lists ─────────────────────────────────────────────────────────────────

    def _render_list(self, lst: List) -> dict:
        return {
            "type":    "list",
            "ordered": bool(lst.ordered),
            "items":   [self._render_list_item(it) for it in (lst.items or [])],
        }

    def _render_list_item(self, item: ListItem) -> dict:
        node: dict = {"content": self._render_runs(item.content or [])}
        children = [self._render_list(child) for child in (item.children or [])]
        if children:
            node["children"] = children
        return node

    # ── Inline ────────────────────────────────────────────────────────────────

    def _render_runs(self, runs: list[TextRun]) -> str:
        parts = []
        for run in runs:
            text = run.text or ""
            if run.bold and run.italic:
                text = f"***{text}***"
            elif run.bold:
                text = f"**{text}**"
            elif run.italic:
                text = f"*{text}*"
            if getattr(run, "strikethrough", False):
                text = f"~~{text}~~"
            if getattr(run, "code", False):
                text = f"`{text}`"
            if getattr(run, "href", None):
                text = f"[{text}]({run.href})"
            parts.append(text)
        return "".join(parts)
