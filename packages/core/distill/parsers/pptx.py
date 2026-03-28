"""
distill.parsers.pptx
~~~~~~~~~~~~~~~~~~~~
Parser for Microsoft PowerPoint presentations (.pptx).

Primary path:   python-pptx (structural slide extraction)
Legacy stub:    .ppt raises ParseError with LibreOffice install hint (Phase 2)

Output schema per slide:
  ## Slide N: {Title}
  [body text / bullet lists]
  [tables as GFM pipe tables]
  > Speaker notes (as blockquote)

Key design decisions:
- Title placeholder (idx 0) text goes into the Section heading, not the body
- Bullet paragraphs (level > 0, or buChar/buAutoNum XML markers) → IR List
- Tables in slide shapes → IR Table; first row treated as header
- Speaker notes appended as BlockQuote at end of each section
- Security: 50 MB input size limit; 500 MB zip bomb limit (.pptx is a ZIP)
- Metadata: all core properties from prs.core_properties
- Word count: sum of all text across all slides and notes

Install:
    pip install distill-core          # includes python-pptx
    pip install distill-core[legacy]  # adds .ppt support via LibreOffice (Phase 2)
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    BlockQuote, Document, DocumentMetadata, Image, ImageType,
    List, ListItem, Paragraph, Section, Table, TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser
from distill.registry import registry


# ── Security constants ────────────────────────────────────────────────────────

_MAX_FILE_BYTES  = 50  * 1024 * 1024   # 50 MB
_MAX_UNZIP_BYTES = 500 * 1024 * 1024   # 500 MB


def _check_input_size(source: Union[str, Path, bytes], max_bytes: int) -> None:
    """Raise ParseError if the source exceeds max_bytes."""
    mb = max_bytes // (1024 * 1024)
    if isinstance(source, (str, Path)):
        size = Path(source).stat().st_size
        if size > max_bytes:
            raise ParseError(
                f"Input file exceeds the {mb} MB size limit "
                f"({size / (1024*1024):.1f} MB). "
                f"Increase the limit via options.extra['max_file_size']."
            )
    elif isinstance(source, bytes):
        if len(source) > max_bytes:
            raise ParseError(
                f"Input file exceeds the {mb} MB size limit "
                f"({len(source) / (1024*1024):.1f} MB). "
                f"Increase the limit via options.extra['max_file_size']."
            )


def _check_zip_bomb(source: Union[str, Path, bytes], max_unzip_bytes: int) -> None:
    """Raise ParseError if the ZIP uncompressed size exceeds max_unzip_bytes."""
    mb = max_unzip_bytes // (1024 * 1024)
    try:
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total = sum(info.file_size for info in zf.infolist())
        if total > max_unzip_bytes:
            raise ParseError(
                f"PPTX archive uncompressed size ({total // (1024*1024)} MB) "
                f"exceeds the {mb} MB safety limit. "
                f"Increase via options.extra['max_unzip_size']."
            )
    except ParseError:
        raise
    except zipfile.BadZipFile:
        raise ParseError("File is not a valid PPTX (ZIP) archive.")
    except Exception as e:
        raise ParseError(f"Could not inspect archive: {e}") from e


# ── Metadata helpers ──────────────────────────────────────────────────────────

def _extract_metadata(prs, path: Optional[Path], slide_count: int) -> DocumentMetadata:
    """
    Pull core properties from prs.core_properties.
    python-pptx uses the same OOXML core-property model as python-docx.
    """
    cp = prs.core_properties

    def _safe(attr: str) -> Optional[str]:
        try:
            v = getattr(cp, attr, None)
            s = str(v).strip() if v is not None else None
            return s if s else None
        except Exception:
            return None

    def _iso(attr: str) -> Optional[str]:
        try:
            v = getattr(cp, attr, None)
            return v.isoformat() if v is not None else None
        except Exception:
            return None

    kw_raw   = _safe("keywords") or ""
    keywords = [k.strip() for k in re.split(r"[,;]", kw_raw) if k.strip()] if kw_raw else []

    # description: python-pptx uses .description (maps to dc:description)
    description = _safe("description") or _safe("comments") or None

    return DocumentMetadata(
        title         = _safe("title"),
        author        = _safe("author"),
        subject       = _safe("subject"),
        description   = description,
        keywords      = keywords,
        created_at    = _iso("created"),
        modified_at   = _iso("modified"),
        slide_count   = slide_count,
        source_format = "pptx",
        source_path   = str(path) if path else None,
    )


def _compute_word_count(prs) -> int:
    """Sum words across all slide text frames and speaker notes."""
    total = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                total += len(shape.text_frame.text.split())
        if slide.has_notes_slide:
            total += len(slide.notes_slide.notes_text_frame.text.split())
    return total


# ── Parser ────────────────────────────────────────────────────────────────────

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

        if isinstance(source, (str, Path)):
            path = Path(source)
        else:
            path = None

        # Security checks
        max_file  = options.extra.get("max_file_size",  _MAX_FILE_BYTES)
        max_unzip = options.extra.get("max_unzip_size", _MAX_UNZIP_BYTES)
        _check_input_size(source, max_file)
        _check_zip_bomb(source, max_unzip)

        try:
            from pptx import Presentation
        except ImportError as e:
            raise ParseError(f"Missing dependency: {e}") from e

        try:
            prs = Presentation(io.BytesIO(source) if isinstance(source, bytes) else str(source))
        except Exception as e:
            raise ParseError(f"python-pptx failed to open presentation: {e}") from e

        slide_count = len(prs.slides)
        metadata    = _extract_metadata(prs, path, slide_count)
        metadata.word_count = _compute_word_count(prs) or None
        document    = Document(metadata=metadata)

        for slide_num, slide in enumerate(prs.slides, start=1):
            section = self._parse_slide(slide, slide_num, options, document)
            document.sections.append(section)

        return document

    # ── Slide parsing ─────────────────────────────────────────────────────────

    def _parse_slide(
        self,
        slide,
        slide_num: int,
        options: ParseOptions,
        doc: Document,
    ) -> Section:
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        title_text = self._get_slide_title(slide)
        heading    = f"Slide {slide_num}: {title_text}" if title_text else f"Slide {slide_num}"

        section = Section(
            heading=[TextRun(text=heading)],
            level=2,
        )

        title_shape = slide.shapes.title

        for shape in slide.shapes:
            # Skip the title shape — its text is already in the heading
            if shape is title_shape:
                continue

            if shape.has_text_frame:
                blocks = self._parse_text_frame(shape.text_frame)
                section.blocks.extend(blocks)

            elif shape.has_table:
                table = self._parse_table(shape.table, options)
                section.blocks.append(table)

            elif hasattr(shape, "shape_type"):
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    img = Image(
                        image_type=ImageType.UNKNOWN,
                        alt_text=shape.name or None,
                    )
                    section.blocks.append(img)

        # Speaker notes appended as a block quote
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
            title = slide.shapes.title
            if title and title.text:
                return title.text.strip()
        except Exception:
            pass
        return None

    def _parse_text_frame(self, text_frame) -> list:
        blocks: list = []
        items:  list = []

        for para in text_frame.paragraphs:
            text = para.text.strip()
            if not text:
                continue

            level = para.level  # 0 = body, 1+ = indented bullet

            runs = [
                TextRun(
                    text   = run.text,
                    bold   = bool(run.font.bold),
                    italic = bool(run.font.italic),
                )
                for run in para.runs
                if run.text
            ]
            if not runs:
                runs = [TextRun(text=text)]

            is_bullet = level > 0 or self._looks_like_bullet(para)

            if is_bullet:
                items.append(ListItem(content=runs))
            else:
                if items:
                    blocks.append(List(items=items))
                    items = []
                blocks.append(Paragraph(runs=runs))

        if items:
            blocks.append(List(items=items))

        return blocks

    def _parse_table(self, tbl, options: ParseOptions) -> Table:
        rows: list[TableRow] = []
        for i, row in enumerate(tbl.rows):
            if options.max_table_rows > 0 and i >= options.max_table_rows:
                break
            cells = [
                TableCell(
                    content=[TextRun(text=cell.text.strip())],
                    is_header=(i == 0),
                )
                for cell in row.cells
            ]
            rows.append(TableRow(cells=cells))
        return Table(rows=rows)

    def _looks_like_bullet(self, para) -> bool:
        """Heuristic: paragraph has a bullet character or auto-number XML marker."""
        try:
            from pptx.oxml.ns import qn
            return (
                para._p.find(qn("a:buChar"))    is not None or
                para._p.find(qn("a:buAutoNum")) is not None
            )
        except Exception:
            return False


# ── Legacy parser ─────────────────────────────────────────────────────────────

@registry.register
class PptLegacyParser(Parser):
    """
    Converts legacy .ppt binary presentations to .pptx via LibreOffice headless,
    then delegates to PptxParser for the actual content extraction.

    Requires LibreOffice to be installed and on PATH (or DISTILL_LIBREOFFICE
    environment variable set to the full binary path).
    """

    extensions            = [".ppt"]
    mime_types            = ["application/vnd.ms-powerpoint"]
    requires              = ["pptx"]
    requires_libreoffice  = True

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        import shutil
        from distill.parsers._libreoffice import convert_via_libreoffice

        options = options or ParseOptions()
        timeout = options.extra.get("libreoffice_timeout", 60)

        output_path = convert_via_libreoffice(source, "pptx", timeout=timeout)
        try:
            doc = PptxParser().parse(output_path, options)
            # Preserve the original source path/format in metadata
            doc.metadata.source_format = "ppt"
            if not isinstance(source, bytes):
                doc.metadata.source_path = str(source)
            return doc
        finally:
            shutil.rmtree(output_path.parent, ignore_errors=True)
