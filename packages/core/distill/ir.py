"""
distill.ir
~~~~~~~~~~
Intermediate Representation (IR) for Distill.

Every format parser produces a Document tree using these dataclasses.
The renderer consumes the same tree to produce Markdown.
The IR is the public API contract between parsers and renderers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional, Union


# ── Enums ────────────────────────────────────────────────────────────────────

class ImageType(str, Enum):
    """Semantic classification of an image in the source document."""
    DECORATIVE  = "decorative"   # logo, background, divider — no semantic value
    CHART       = "chart"        # bar/line/pie chart — underlying data may be available
    DIAGRAM     = "diagram"      # flowchart, org chart, SmartArt
    SCREENSHOT  = "screenshot"   # UI or code screenshot — OCR candidate
    PHOTO       = "photo"        # photograph or illustration
    TABLE       = "table"        # table rendered as an image (common in scanned PDFs)
    UNKNOWN     = "unknown"      # could not be classified


class Alignment(str, Enum):
    LEFT    = "left"
    CENTER  = "center"
    RIGHT   = "right"
    NONE    = "none"


# ── Inline nodes ─────────────────────────────────────────────────────────────

@dataclass
class TextRun:
    """A run of inline text with optional formatting."""
    text:       str
    bold:       bool            = False
    italic:     bool            = False
    code:       bool            = False   # inline code
    strikethrough: bool         = False
    href:       Optional[str]   = None    # hyperlink URL


# ── Block nodes ──────────────────────────────────────────────────────────────

@dataclass
class Image:
    """
    An image extracted from the source document.

    The renderer picks the richest available representation in priority order:
    structured_data > ocr_text > caption > alt_text > (suppress if decorative)
    """
    image_type:       ImageType             = ImageType.UNKNOWN
    path:             Optional[str]         = None   # path to extracted image file
    alt_text:         Optional[str]         = None   # original alt text (often empty)
    caption:          Optional[str]         = None   # vision-model generated description
    ocr_text:         Optional[str]         = None   # OCR result for text-heavy images
    structured_data:  Optional[list]        = None   # chart/table data as list of row dicts


@dataclass
class TableCell:
    content:    list[Union[TextRun, "Paragraph"]] = field(default_factory=list)
    colspan:    int                               = 1
    rowspan:    int                               = 1
    alignment:  Alignment                         = Alignment.NONE
    is_header:  bool                              = False


@dataclass
class TableRow:
    cells: list[TableCell] = field(default_factory=list)


@dataclass
class Table:
    rows:        list[TableRow]        = field(default_factory=list)
    caption:     Optional[str]         = None
    truncated:   bool                  = False   # True if rows were capped
    total_rows:  Optional[int]         = None    # original row count before truncation


@dataclass
class ListItem:
    content:  list[TextRun]      = field(default_factory=list)
    children: list["List"]       = field(default_factory=list)  # nested lists


@dataclass
class List:
    items:    list[ListItem]  = field(default_factory=list)
    ordered:  bool            = False


@dataclass
class CodeBlock:
    code:     str
    language: Optional[str]   = None


@dataclass
class BlockQuote:
    content: list["Block"] = field(default_factory=list)


@dataclass
class Paragraph:
    runs:      list[TextRun]   = field(default_factory=list)
    alignment: Alignment       = Alignment.NONE


# Union type for anything that can appear as a block in a Section or Document
Block = Union[Paragraph, Table, List, CodeBlock, BlockQuote, Image]


# ── Structure nodes ───────────────────────────────────────────────────────────

@dataclass
class Section:
    """
    A document section introduced by a heading.
    level=1 maps to H1, level=2 to H2, etc.
    level=0 means a top-level container with no heading (e.g. document preamble).
    """
    heading:     Optional[list[TextRun]]  = None
    level:       int                      = 1
    blocks:      list[Block]              = field(default_factory=list)
    subsections: list["Section"]          = field(default_factory=list)


# ── Document metadata ────────────────────────────────────────────────────────

@dataclass
class DocumentMetadata:
    title:          Optional[str]       = None
    author:         Optional[str]       = None
    created_at:     Optional[str]       = None   # ISO 8601
    modified_at:    Optional[str]       = None   # ISO 8601
    subject:        Optional[str]       = None
    description:    Optional[str]       = None
    keywords:       list[str]           = field(default_factory=list)
    page_count:     Optional[int]       = None
    slide_count:    Optional[int]       = None   # PPTX
    sheet_count:    Optional[int]       = None   # XLSX
    word_count:     Optional[int]       = None
    language:       Optional[str]       = None   # BCP-47 language tag e.g. "en-US"
    source_format:  Optional[str]       = None   # e.g. "docx", "pdf", "pptx"
    source_path:    Optional[str]       = None


# ── Root node ────────────────────────────────────────────────────────────────

@dataclass
class Document:
    """
    Root node of the IR tree.
    A Document contains a flat list of top-level Sections.
    Nesting is expressed via Section.subsections, not via Document.
    """
    metadata:  DocumentMetadata       = field(default_factory=DocumentMetadata)
    sections:  list[Section]          = field(default_factory=list)
    warnings:  list[str]              = field(default_factory=list)

    def render(self, **options) -> str:
        """Convenience method: render this Document to Markdown."""
        from distill.renderer import MarkdownRenderer
        return MarkdownRenderer(**options).render(self)

    def render_stream(self, **options) -> Iterator[str]:
        """Convenience method: stream this Document as Markdown chunks."""
        from distill.renderer import MarkdownRenderer
        yield from MarkdownRenderer(**options).render_stream(self)
