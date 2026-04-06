"""
distill.renderers.chunks
~~~~~~~~~~~~~~~~~~~~~~~~
Renders an IR Document as a flat list of RAG-ready Chunk objects.

Each chunk corresponds to a semantic unit (section, table, list) and
carries enough context (heading path, source document, token count) to
be inserted directly into a vector database without further splitting.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from distill.ir import Block, Document, Paragraph, Section, Table, TextRun


@dataclass
class Chunk:
    """A single RAG-ready content chunk."""
    chunk_id:        str
    type:            str            # "section" | "table" | "list" | "audio_turn"
    heading_path:    str            # ancestor heading chain, e.g. "Intro > Background"
    content:         str            # Markdown-rendered content
    source_document: str
    source_format:   str
    token_count:     int            # estimated: len(content) // 4
    page_start:      Optional[int]  = field(default=None)
    page_end:        Optional[int]  = field(default=None)
    timestamp_start: Optional[float] = field(default=None)   # audio only
    timestamp_end:   Optional[float] = field(default=None)   # audio only

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict; omit None optional fields."""
        d: dict = {
            "chunk_id":        self.chunk_id,
            "type":            self.type,
            "heading_path":    self.heading_path,
            "content":         self.content,
            "source_document": self.source_document,
            "source_format":   self.source_format,
            "token_count":     self.token_count,
        }
        if self.page_start is not None:
            d["page_start"] = self.page_start
        if self.page_end is not None:
            d["page_end"] = self.page_end
        if self.timestamp_start is not None:
            d["timestamp_start"] = self.timestamp_start
        if self.timestamp_end is not None:
            d["timestamp_end"] = self.timestamp_end
        return d


_MAX_TOKENS = 800   # estimated token threshold for splitting a section


class ChunksRenderer:
    """Converts an IR Document into a flat list of Chunk objects."""

    def render(
        self,
        doc: Document,
        source_document: str,
        source_format: str,
    ) -> list[Chunk]:
        """
        Traverse all sections and produce one Chunk per semantic unit.

        Rules:
        - Each Section → one chunk (heading + immediate blocks).
        - Table blocks are always a single chunk regardless of row count.
        - Sections exceeding _MAX_TOKENS are split at Paragraph boundaries.
        - The parent heading path is prepended to every child chunk's content.
        """
        source_document = source_document or ""
        source_format   = source_format or ""
        doc_hash        = hashlib.md5(source_document.encode()).hexdigest()[:8]
        counter         = [0]   # mutable int via list so nested helpers can increment

        chunks: list[Chunk] = []
        for section in (doc.sections if doc.sections else []):
            chunks.extend(
                self._section_chunks(section, "", doc_hash, source_document, source_format, counter)
            )
        return chunks

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _make_id(self, doc_hash: str, counter: list[int]) -> str:
        cid = f"{doc_hash}_{counter[0]:04d}"
        counter[0] += 1
        return cid

    def _heading_text(self, section: Section) -> str:
        if not section.heading:
            return ""
        from distill.renderer import MarkdownRenderer
        renderer = MarkdownRenderer()
        return renderer._render_runs(section.heading)

    def _build_path(self, parent_path: str, section: Section) -> str:
        h = self._heading_text(section)
        if not h:
            return parent_path
        return f"{parent_path} > {h}".lstrip(" > ")

    def _render_block(self, block: Block) -> str:
        from distill.renderer import MarkdownRenderer
        return MarkdownRenderer()._render_block(block)

    def _section_chunks(
        self,
        section: Section,
        parent_path: str,
        doc_hash: str,
        source_document: str,
        source_format: str,
        counter: list[int],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        current_path = self._build_path(parent_path, section)
        heading_prefix = (f"{'#' * max(section.level, 1)} {self._heading_text(section)}\n\n"
                          if self._heading_text(section) else "")

        # Separate table blocks from everything else; preserve order
        pending_blocks: list[Block] = []

        def flush_pending() -> None:
            if not pending_blocks:
                return
            content_parts = [self._render_block(b) for b in pending_blocks]
            content_parts = [p for p in content_parts if p.strip()]
            if not content_parts:
                pending_blocks.clear()
                return
            combined = "\n\n".join(content_parts)
            full_content = heading_prefix + combined if not chunks else combined
            # Split if over token threshold
            for chunk_content in self._split_if_needed(full_content, pending_blocks, heading_prefix if not chunks else ""):
                chunks.append(Chunk(
                    chunk_id        = self._make_id(doc_hash, counter),
                    type            = "section",
                    heading_path    = current_path,
                    content         = chunk_content,
                    source_document = source_document,
                    source_format   = source_format,
                    token_count     = len(chunk_content) // 4,
                ))
            pending_blocks.clear()

        for block in (section.blocks if section.blocks else []):
            if isinstance(block, Table):
                flush_pending()
                table_md = self._render_block(block)
                chunks.append(Chunk(
                    chunk_id        = self._make_id(doc_hash, counter),
                    type            = "table",
                    heading_path    = current_path,
                    content         = table_md,
                    source_document = source_document,
                    source_format   = source_format,
                    token_count     = len(table_md) // 4,
                ))
            else:
                pending_blocks.append(block)

        flush_pending()

        # If the section had no blocks at all, emit a heading-only chunk
        if not chunks and self._heading_text(section):
            chunks.append(Chunk(
                chunk_id        = self._make_id(doc_hash, counter),
                type            = "section",
                heading_path    = current_path,
                content         = heading_prefix.strip(),
                source_document = source_document,
                source_format   = source_format,
                token_count     = len(heading_prefix) // 4,
            ))

        # Recurse into subsections
        for sub in (section.subsections if section.subsections else []):
            chunks.extend(
                self._section_chunks(sub, current_path, doc_hash, source_document, source_format, counter)
            )

        return chunks

    def _split_if_needed(
        self,
        combined: str,
        blocks: list[Block],
        heading_prefix: str,
    ) -> list[str]:
        """
        If combined content exceeds _MAX_TOKENS, split at Paragraph boundaries.
        Returns a list of content strings (may be one if no split needed).
        """
        if len(combined) // 4 <= _MAX_TOKENS:
            return [combined]

        # Re-render each block individually and bin-pack into chunks
        rendered = [self._render_block(b) for b in blocks]
        rendered = [r for r in rendered if r.strip()]

        result: list[str] = []
        current_parts: list[str] = []
        current_tokens = len(heading_prefix) // 4

        for part in rendered:
            part_tokens = len(part) // 4
            if current_parts and current_tokens + part_tokens > _MAX_TOKENS:
                prefix = heading_prefix if not result else ""
                result.append(prefix + "\n\n".join(current_parts))
                current_parts = [part]
                current_tokens = part_tokens
            else:
                current_parts.append(part)
                current_tokens += part_tokens

        if current_parts:
            prefix = heading_prefix if not result else ""
            result.append(prefix + "\n\n".join(current_parts))

        return result if result else [combined]
