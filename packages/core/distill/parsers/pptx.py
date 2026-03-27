"""
distill.parsers.pptx
~~~~~~~~~~~~~~~~~~~~
Parser for Microsoft PowerPoint presentations (.pptx, .ppt).

Primary path:   python-pptx (structural slide extraction)
Legacy path:    LibreOffice headless (.ppt → .pptx pre-conversion)

Output schema per slide:
  ## Slide N: {Title}
  [body text / bullets]
  [tables as GFM pipe tables]
  > Speaker notes (as blockquote)

Install:
    pip install distill-core          # includes python-pptx
    pip install distill-core[legacy]  # adds .ppt support via LibreOffice
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    BlockQuote, Document, DocumentMetadata, Image, ImageType,
    List, ListItem, Paragraph, Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser
from distill.registry import registry


@registry.register
class PptxParser(Parser):
    """Parses .pptx files using python-pptx."""

    extensions = [".pptx"]
    mime_types = [
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ]
    requires          = ["pptx"]    # python-pptx (import name: pptx)
    optional_requires = []

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        try:
            from pptx import Presentation
            from pptx.util import Pt
        except ImportError as e:
            raise ParseError(f"Missing dependency: {e}") from e

        try:
            if isinstance(source, bytes):
                import io
                prs = Presentation(io.BytesIO(source))
            else:
                prs = Presentation(str(source))
        except Exception as e:
            raise ParseError(f"python-pptx failed to open presentation: {e}") from e

        metadata = DocumentMetadata(
            source_format = "pptx",
            source_path   = str(source) if not isinstance(source, bytes) else None,
            slide_count   = len(prs.slides),
        )
        document = Document(metadata=metadata)

        for slide_num, slide in enumerate(prs.slides, start=1):
            section = self._parse_slide(slide, slide_num, options, document)
            document.sections.append(section)

        return document

    def _parse_slide(self, slide, slide_num: int, options: ParseOptions, doc: Document) -> Section:
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        title_text = self._get_slide_title(slide)
        heading    = f"Slide {slide_num}: {title_text}" if title_text else f"Slide {slide_num}"

        section = Section(
            heading = [TextRun(text=heading)],
            level   = 2,
        )

        for shape in slide.shapes:
            # Skip title placeholder (already in heading)
            if hasattr(shape, "placeholder_format") and shape.placeholder_format:
                if shape.placeholder_format.idx == 0:
                    continue

            if shape.has_text_frame:
                blocks = self._parse_text_frame(shape.text_frame)
                section.blocks.extend(blocks)

            elif shape.has_table:
                table = self._parse_table(shape.table)
                section.blocks.append(table)

            elif hasattr(shape, "shape_type"):
                # Images / pictures
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    img = Image(
                        image_type = ImageType.UNKNOWN,
                        alt_text   = shape.name or None,
                    )
                    section.blocks.append(img)

        # Speaker notes
        if slide.has_notes_slide:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                bq = BlockQuote(content=[
                    Paragraph(runs=[TextRun(text=notes_text)])
                ])
                section.blocks.append(bq)

        return section

    def _get_slide_title(self, slide) -> Optional[str]:
        try:
            if slide.shapes.title and slide.shapes.title.text:
                return slide.shapes.title.text.strip()
        except Exception:
            pass
        return None

    def _parse_text_frame(self, text_frame) -> list:
        blocks = []
        items  = []

        for para in text_frame.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            level = para.level  # indentation level for bullets

            runs = [TextRun(
                text   = run.text,
                bold   = bool(run.font.bold),
                italic = bool(run.font.italic),
            ) for run in para.runs if run.text]

            if not runs:
                runs = [TextRun(text=text)]

            if level > 0 or self._looks_like_bullet(para):
                items.append(ListItem(content=runs))
            else:
                if items:
                    blocks.append(List(items=items))
                    items = []
                blocks.append(Paragraph(runs=runs))

        if items:
            blocks.append(List(items=items))

        return blocks

    def _parse_table(self, tbl) -> Table:
        rows = []
        for i, row in enumerate(tbl.rows):
            cells = [
                TableCell(
                    content   = [TextRun(text=cell.text.strip())],
                    is_header = (i == 0),
                )
                for cell in row.cells
            ]
            rows.append(TableRow(cells=cells))
        return Table(rows=rows)

    def _looks_like_bullet(self, para) -> bool:
        """Heuristic: paragraph uses a list/bullet XML element."""
        try:
            from pptx.oxml.ns import qn
            return para._p.find(qn("a:buChar")) is not None or \
                   para._p.find(qn("a:buAutoNum")) is not None
        except Exception:
            return False
