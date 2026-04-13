"""
distill.parsers.pdf
~~~~~~~~~~~~~~~~~~~
Parser for PDF documents — both native text and scanned.

Routing strategy (quality-gated cascade):
  1. pdfplumber  — native text PDFs (tables + text, header/footer suppressed)
  2. docling     — scanned / complex PDFs (Phase 4)
  3. Tesseract   — lightweight OCR fallback (Phase 4)

Install:
    pip install distill-core        # includes pdfplumber
    pip install distill-core[ocr]   # adds docling + Tesseract for scanned PDFs
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

from distill.ir import (
    Document, DocumentMetadata, Image, ImageType, Paragraph, Section, Table,
    TableCell, TableRow, TextRun,
)
from distill.parsers.base import ParseError, ParseOptions, Parser, extract_image
from distill.registry import registry


# ── Security constants ────────────────────────────────────────────────────────

_MAX_FILE_BYTES = 50 * 1024 * 1024   # 50 MB input limit


def _check_input_size(source, max_bytes: int) -> None:
    """Raise ParseError if the input exceeds the configured size limit."""
    if isinstance(source, bytes):
        size = len(source)
    else:
        try:
            size = Path(source).stat().st_size
        except OSError:
            return
    if size > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise ParseError(
            f"Input file exceeds the {mb} MB size limit "
            f"({size / 1024 / 1024:.1f} MB). "
            f"Increase via options.extra['max_file_size']."
        )


# ── PDF date parsing ──────────────────────────────────────────────────────────

def _parse_pdf_date(raw: Optional[str]) -> Optional[str]:
    """
    Convert a PDF date string (D:YYYYMMDDHHmmSSOHH'mm') to ISO 8601.
    Returns None if the string cannot be parsed.
    """
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("D:"):
        s = s[2:]
    match = re.match(
        r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?([Z+\-].+)?", s
    )
    if not match:
        return None
    year, month, day, hour, minute = match.group(1, 2, 3, 4, 5)
    second = match.group(6) or "00"
    tz_raw = (match.group(7) or "Z").strip()
    try:
        dt = f"{year}-{month}-{day}T{hour}:{minute}:{second}"
        if tz_raw in ("Z", ""):
            return dt + "+00:00"
        tz_match = re.match(r"([+\-])(\d{2})[':']?(\d{2})'?", tz_raw)
        if tz_match:
            sign, hh, mm = tz_match.groups()
            return f"{dt}{sign}{hh}:{mm}"
        return dt + "+00:00"
    except Exception:
        return None


# ── Metadata extraction ───────────────────────────────────────────────────────

def _extract_metadata(pdf, source) -> DocumentMetadata:
    """
    Build DocumentMetadata from a pdfplumber PDF object.
    All fields are best-effort; missing values stay None / empty.
    """
    meta = DocumentMetadata(
        source_format="pdf",
        source_path=str(source) if not isinstance(source, bytes) else None,
        page_count=len(pdf.pages),
    )
    info = pdf.metadata or {}

    def _str(key: str) -> Optional[str]:
        val = info.get(key)
        if isinstance(val, bytes):
            try:
                val = val.decode("utf-8", errors="replace")
            except Exception:
                return None
        return (val or "").strip() or None

    meta.title       = _str("Title")
    meta.author      = _str("Author")
    meta.subject     = _str("Subject")
    meta.description = _str("Subject")   # PDF has no separate description field
    meta.created_at  = _parse_pdf_date(_str("CreationDate"))
    meta.modified_at = _parse_pdf_date(_str("ModDate"))

    raw_kw = _str("Keywords")
    if raw_kw:
        meta.keywords = [k.strip() for k in re.split(r"[,;]", raw_kw) if k.strip()]

    return meta


# ── Text helpers ──────────────────────────────────────────────────────────────

_PAGE_NUMBER_RE = re.compile(
    r"^\s*[\|\-]?\s*[Pp]age\s+\d+\s*$"           # "Page 5", "| Page 5"
    r"|^\s*[\|\-]?\s*\d+\s*(of\s+\d+)?\s*[\|\-]?\s*$"  # "5", "| 6", "5 of 20", "- 5 -"
)


def _extract_page_text(page) -> str:
    """
    Extract text from a page, suppressing:
      - header region (top 5 % of page height)
      - footer region (bottom 8 % of page height)
      - regions covered by detected tables
    """
    h = page.height
    w = page.width

    # Crop to body region (exclude header / footer margins)
    body = page.crop((0, h * 0.05, w, h * 0.90))

    # Exclude table bounding boxes from text extraction
    try:
        tables = body.find_tables()
        for tbl in tables:
            body = body.outside_bbox(tbl.bbox)
    except Exception:
        pass

    return body.extract_text() or ""


def _correct_rotated_text(page, raw_text: str) -> str:
    """Detect rotated character runs in a pdfplumber page and reverse them
    in the extracted text string to restore correct reading order.

    Rotated text (90° / 270°) is stored right-to-left in the PDF content
    stream, causing pdfplumber to extract it reversed. This function detects
    runs of rotated characters via their transformation matrix and corrects
    the reversal before the text reaches the paragraph splitter.

    Returns raw_text unchanged if no rotated characters are found.
    """
    chars = page.chars
    if not chars:
        return raw_text

    # Classify each character as rotated or normal.
    # matrix = (a, b, c, d, e, f) — a 2D transformation matrix.
    # Rotated 90°/270°: a ≈ 0, d ≈ 0, b and c non-zero.
    # Normal horizontal: a > 0, d > 0, b ≈ 0, c ≈ 0.
    NEAR_ZERO = 0.01
    X_TOLERANCE = 5  # EMU — group chars into same vertical run

    rotated_chars = []
    for ch in chars:
        matrix = ch.get("matrix")
        if not matrix or len(matrix) < 4:
            continue
        a, b, c, d = matrix[0], matrix[1], matrix[2], matrix[3]
        if abs(a) < NEAR_ZERO and abs(d) < NEAR_ZERO and abs(b) > NEAR_ZERO:
            rotated_chars.append(ch)

    if not rotated_chars:
        return raw_text

    # Group consecutive rotated characters into vertical runs by x-position.
    # Characters in the same vertical text run share approximately the same x.
    runs = []
    current_run = [rotated_chars[0]]
    for ch in rotated_chars[1:]:
        prev_x = current_run[-1].get("x0", 0)
        curr_x = ch.get("x0", 0)
        if abs(curr_x - prev_x) <= X_TOLERANCE:
            current_run.append(ch)
        else:
            runs.append(current_run)
            current_run = [ch]
    runs.append(current_run)

    # For each run of 2+ characters, reverse and replace in raw_text.
    corrected = raw_text
    for run in runs:
        if len(run) < 2:
            continue
        wrong = "".join(ch.get("text", "") for ch in run)
        right = wrong[::-1]
        if wrong in corrected:
            corrected = corrected.replace(wrong, right, 1)

    return corrected


FONT_ENCODING_CORRUPTION_THRESHOLD = 0.08  # 8%


def _detect_encoding_corruption(text: str) -> float:
    """Return ratio of likely-corrupted characters to total non-whitespace chars.
    Returns 0.0 if text is empty or has no non-whitespace content."""
    non_ws = [ch for ch in text if not ch.isspace()]
    if not non_ws:
        return 0.0
    corrupted = sum(
        1 for ch in non_ws
        if ch == '\ufffd'
        or (ord(ch) < 32 and ch not in '\t\n\r')
        or (0xE000 <= ord(ch) <= 0xF8FF)
    )
    return corrupted / len(non_ws)


def _build_line_font_map(page) -> dict[int, float]:
    """Build a map of {y_bucket: max_font_size} from page.chars.

    Groups characters into lines by rounding their top coordinate to the
    nearest 2 points (y_bucket). Returns the maximum font size seen on each
    line. Used by _chars_to_blocks() to detect heading lines.
    """
    line_sizes: dict[int, float] = {}
    for ch in (page.chars or []):
        size = ch.get("size") or 0
        top = ch.get("top") or 0
        bucket = round(top / 2) * 2  # group within 2pt vertical bands
        if size > line_sizes.get(bucket, 0):
            line_sizes[bucket] = size
    return line_sizes


def _chars_to_blocks(text: str, line_font_map: dict[int, float]) -> list:
    """Convert extracted text to IR blocks, promoting heading lines.

    Uses line_font_map (from _build_line_font_map) to detect lines with
    font size significantly above the body median. Those lines become
    Section heading markers; all other lines become Paragraph nodes.

    Falls back to _text_to_paragraphs() behaviour when line_font_map is
    empty (scanned PDFs, OCR output).
    """
    if not line_font_map:
        return _text_to_paragraphs(text)

    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []

    # Compute body font size: median of all line sizes
    sizes = sorted(line_font_map.values())
    median_size = sizes[len(sizes) // 2] if sizes else 0

    # A line is a heading candidate if its font size >= median * 1.4
    # and it is short enough to be a heading (< 120 chars)
    HEADING_SIZE_RATIO = 1.4
    MAX_HEADING_LEN = 120

    blocks: list = []
    # Match lines to y-buckets by iterating in order
    sorted_buckets = sorted(line_font_map.keys())
    bucket_iter = iter(sorted_buckets)
    current_bucket = next(bucket_iter, None)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Advance bucket to find font size for this line
        line_size = line_font_map.get(current_bucket, 0) if current_bucket is not None else 0
        current_bucket = next(bucket_iter, current_bucket)

        is_heading = (
            line_size >= median_size * HEADING_SIZE_RATIO
            and len(stripped) <= MAX_HEADING_LEN
            and not stripped.isdigit()  # exclude bare page numbers
        )

        if is_heading:
            ratio = line_size / median_size if median_size > 0 else 1
            if ratio >= 2.0:
                level = 1
            elif ratio >= 1.6:
                level = 2
            else:
                level = 3
            blocks.append(Section(
                level=level,
                heading=[TextRun(text=stripped)],
            ))
        else:
            blocks.append(Paragraph(runs=[TextRun(text=stripped)]))

    return blocks


def _text_to_paragraphs(text: str) -> list[Paragraph]:
    """Convert a block of plain text into Paragraph IR nodes, skipping page numbers."""
    paragraphs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _PAGE_NUMBER_RE.match(line):
            continue
        paragraphs.append(Paragraph(runs=[TextRun(text=line)]))
    return paragraphs


# ── Table extraction ──────────────────────────────────────────────────────────

import logging as _logging

_log = _logging.getLogger(__name__)


def _build_ir_table(raw_rows: list[list[str]], max_rows: int) -> "Table | None":
    """Build an IR Table node from a list of rows of string values.

    Shared by both the words-based and legacy extraction paths.
    """
    if not raw_rows:
        return None

    # Pre-processing: strip prose-only prefix rows from hybrid tables.
    # pdfplumber sometimes merges a prose section above a real table into
    # one detected table region. Prose-only rows have content in exactly
    # one column and that content is sentence-length (>80 chars average).
    # Strip these prefix rows; if nothing remains, return None.
    if raw_rows:
        num_cols_check = max(len(row) for row in raw_rows)
        if num_cols_check >= 2:
            # Find where real tabular data starts: first row that has
            # content in more than one column
            first_data_row = None
            for idx, row in enumerate(raw_rows):
                cols_with_content = sum(
                    1 for cell in row if cell.strip()
                )
                if cols_with_content > 1:
                    first_data_row = idx
                    break
            if first_data_row is not None and first_data_row > 0:
                # Check if the prefix rows are prose (avg cell len > 80)
                prefix_cells = [
                    cell for row in raw_rows[:first_data_row]
                    for cell in row if cell.strip()
                ]
                if prefix_cells:
                    prefix_avg = sum(len(c) for c in prefix_cells) / len(prefix_cells)
                    if prefix_avg > 80:
                        # Strip prose prefix — keep only real data rows
                        raw_rows = raw_rows[first_data_row:]
            elif first_data_row is None:
                # No row has content in more than one column — entire
                # table is effectively single-column prose, handle below
                pass

    # Filter 1: all cells empty — decorative box or logo detected as table
    if all(
        str(cell or "").strip() == ""
        for row in raw_rows
        for cell in row
    ):
        return None

    # Filter 2: majority phantom columns — accent bar ghost table
    num_cols = max(len(row) for row in raw_rows)
    if num_cols > 0:
        phantom_cols = 0
        for col_idx in range(num_cols):
            col_values = [
                str(row[col_idx] or "").strip() if col_idx < len(row) else ""
                for row in raw_rows
            ]
            if all(v == "" for v in col_values):
                phantom_cols += 1
        if phantom_cols > num_cols / 2:
            return None

    # Filter 3: single-column or effectively-single-column large text block
    # Catches page borders and text-box frames detected as tables.
    # A table is "effectively single-column" if only one column has any
    # non-empty content — the rest are phantom columns with invisible content
    # that slipped through Filter 2.
    if raw_rows:
        num_cols_f3 = max(len(row) for row in raw_rows)
        cols_with_content = 0
        for col_idx in range(num_cols_f3):
            col_values = [
                row[col_idx].strip() if col_idx < len(row) else ""
                for row in raw_rows
            ]
            if any(v for v in col_values):
                cols_with_content += 1
        if cols_with_content == 1:
            total_text = " ".join(
                cell for row in raw_rows for cell in row
                if cell.strip()
            )
            if len(total_text) > 200:
                return None

    # Filter 4: prose-in-cells — page border or layout box detected as table
    # Only applies to narrow tables (<=3 cols); wide tables are real data tables.
    # If average non-empty cell length exceeds 80 chars, cells contain prose
    # rather than tabular values — this is a layout false positive.
    if raw_rows:
        num_cols_f4 = max(len(row) for row in raw_rows)
        if num_cols_f4 <= 3:
            all_cells = [
                cell for row in raw_rows
                for cell in row
                if cell.strip()
            ]
            if all_cells:
                avg_len = sum(len(c) for c in all_cells) / len(all_cells)
                if avg_len > 80:
                    return None

    truncated = max_rows and len(raw_rows) > max_rows
    total_rows = len(raw_rows) if truncated else None
    if truncated:
        raw_rows = raw_rows[:max_rows]

    rows: list[TableRow] = []
    for i, row in enumerate(raw_rows):
        cells = [
            TableCell(
                content=[Paragraph(runs=[TextRun(text=str(cell or "").strip())])],
                is_header=(i == 0),
            )
            for cell in row
        ]
        rows.append(TableRow(cells=cells))

    return Table(rows=rows, truncated=bool(truncated), total_rows=total_rows)


def _extract_tables(page, max_rows: int = 500) -> list:
    """Extract tables from a pdfplumber page as IR Table nodes.

    Uses find_tables() + per-cell extract_words() to prevent mid-word splits
    that occur when extract_tables() bisects words at column boundaries.
    Falls back to extract_tables() on any exception.
    """
    try:
        return _extract_tables_words(page, max_rows)
    except Exception as exc:
        _log.warning("_extract_tables_words failed (%s); falling back to extract_tables()", exc)
        return _extract_tables_legacy(page, max_rows)


def _extract_tables_words(page, max_rows: int) -> list:
    """Primary path: find_tables() + extract_words() per cell."""
    tables = page.find_tables()
    if not tables:
        return []

    result = []
    for tbl in tables:
        raw_rows = []
        for row in tbl.rows:
            row_values = []
            for bbox in row.cells:
                if bbox is None:
                    row_values.append("")
                else:
                    try:
                        cropped = page.crop(bbox)
                        words = cropped.extract_words(keep_blank_chars=False)
                        cell_value = " ".join(w["text"] for w in words).strip()
                    except Exception:
                        cell_value = ""
                    row_values.append(cell_value)
            raw_rows.append(row_values)

        ir_table = _build_ir_table(raw_rows, max_rows)
        if ir_table is not None:
            result.append(ir_table)

    return result


def _extract_tables_legacy(page, max_rows: int) -> list:
    """Fallback path: original extract_tables() behaviour."""
    tables: list[Table] = []
    try:
        raw_tables = page.extract_tables() or []
    except Exception:
        return tables

    for tbl_data in raw_tables:
        if not tbl_data:
            continue
        ir_table = _build_ir_table(tbl_data, max_rows)
        if ir_table is not None:
            tables.append(ir_table)

    return tables


# ── Parser ────────────────────────────────────────────────────────────────────

@registry.register
class PdfParser(Parser):
    """
    Parses PDF files.

    Native PDFs: pdfplumber with table-region filtering and header/footer suppression.
    Scanned PDFs: routed to docling / Tesseract (Phase 4).
    """

    extensions        = [".pdf"]
    mime_types        = ["application/pdf"]
    requires          = ["pdfplumber"]
    optional_requires = ["docling", "pytesseract", "pdf2image"]

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        try:
            import pdfplumber
        except ImportError as e:
            raise ParseError(f"pdfplumber not available: {e}") from e

        # ── Security: input size check ────────────────────────────────────────
        max_file = options.extra.get("max_file_size", _MAX_FILE_BYTES)
        _check_input_size(source, max_file)

        try:
            if isinstance(source, bytes):
                import io
                pdf = pdfplumber.open(io.BytesIO(source))
            else:
                pdf = pdfplumber.open(str(source))
        except Exception as e:
            msg = str(e).lower()
            if "password" in msg or "encrypted" in msg:
                raise ParseError(
                    "PDF is password-protected. "
                    "Distill cannot process encrypted PDFs."
                ) from e
            raise ParseError(f"Could not open PDF: {e}") from e

        with pdf:
            page_count = len(pdf.pages)
            document   = self._parse_native(pdf, source, options)

            # Quality gate: if native extraction yielded too few words per page,
            # the PDF is likely scanned (image-only) — route to the OCR pipeline.
            # OCR is opt-in: callers must set options.extra['enable_ocr'] = True.
            from distill.parsers._ocr import is_scanned_pdf, ocr_pdf

            if is_scanned_pdf(document, page_count):
                # Determine effective OCR-enabled state: ParseOptions.ocr_enabled
                # takes precedence, then fall back to extra['enable_ocr']
                ocr_on = options.ocr_enabled and options.extra.get("enable_ocr", options.ocr_enabled)

                if ocr_on:
                    document.warnings.append(
                        "Sparse text layer detected — running OCR pipeline"
                    )
                    try:
                        return ocr_pdf(source, options)
                    except ParseError as ocr_err:
                        document.warnings.append(f"OCR not available: {ocr_err}")
                        return document
                else:
                    # Scanned PDF with OCR disabled — signal OCR_REQUIRED
                    from distill import ParserOutcome
                    document.parser_outcome = ParserOutcome.OCR_REQUIRED
                    document.warnings.append(
                        "Sparse text layer detected — this PDF may be scanned. "
                        "Enable OCR in Options for better results."
                    )

            return document

    # ── Native PDF ───────────────────────────────────────────────────────────

    def _parse_native(self, pdf, source, options: ParseOptions) -> Document:
        metadata = _extract_metadata(pdf, source)
        document = Document(metadata=metadata)
        max_rows = options.max_table_rows

        # Capture source word count before IR mapping
        try:
            total_words = 0
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                total_words += len(page_text.split())
            document.metadata.word_count = total_words or None
        except Exception:
            pass

        # Math detection — scan character data for math fonts/symbols
        if options and options.collector:
            try:
                from distill.features.math_detection import MathDetector
                char_data = []
                for page_num_md, page_md in enumerate(pdf.pages, start=1):
                    for char in (page_md.chars or []):
                        char_data.append({
                            "fontname": char.get("fontname", ""),
                            "text": char.get("text", ""),
                            "page_number": page_num_md,
                        })
                if char_data:
                    MathDetector().detect_in_pdf(char_data, options.collector)
            except Exception:
                pass

        prev_page_last_table = None   # IR Table node — last table from previous page
        prev_page_num = None          # int — page number of prev_page_last_table
        corrupted_pages = []          # pages with suspected font encoding corruption

        for page_num, page in enumerate(pdf.pages, start=1):
            section = Section(
                heading=[TextRun(text=f"Page {page_num}")],
                level=2,
            )

            # Tables first (bbox-aware)
            page_tables = _extract_tables(page, max_rows=max_rows)
            for table in page_tables:
                section.blocks.append(table)

            # Detect cross-page table continuation
            if page_tables and prev_page_last_table is not None:
                first_table_this_page = page_tables[0]
                prev_cols = (
                    len(prev_page_last_table.rows[0].cells)
                    if prev_page_last_table.rows else 0
                )
                curr_cols = (
                    len(first_table_this_page.rows[0].cells)
                    if first_table_this_page.rows else 0
                )
                if prev_cols > 0 and curr_cols > 0 and prev_cols == curr_cols:
                    if options.collector:
                        from distill.warnings import ConversionWarning, WarningType
                        options.collector.add(ConversionWarning(
                            type=WarningType.CROSS_PAGE_TABLE,
                            message=(
                                f"Table on page {prev_page_num} may continue onto "
                                f"page {page_num}. Column counts match "
                                f"({prev_cols} columns). Rows may be missing headers."
                            ),
                            pages=[prev_page_num, page_num],
                        ))

            # Update tracking variables for next iteration
            # Reset when a page has no tables so only adjacent pages trigger
            prev_page_last_table = page_tables[-1] if page_tables else None
            prev_page_num = page_num if page_tables else None

            # Text outside table regions, with header/footer suppression
            text = _extract_page_text(page)
            text = _correct_rotated_text(page, text)
            if _detect_encoding_corruption(text) > FONT_ENCODING_CORRUPTION_THRESHOLD:
                corrupted_pages.append(page_num)
            line_font_map = _build_line_font_map(page)
            for block in _chars_to_blocks(text, line_font_map):
                section.blocks.append(block)

            # Image extraction wiring
            if options.images != "suppress":
                source_stem = (
                    Path(str(source)).stem
                    if not isinstance(source, bytes)
                    else "file"
                )
                try:
                    from distill.parsers.base import classify_image

                    for img_idx, img_info in enumerate(page.images or []):
                        # Classify before stream read
                        img_w = img_info.get("x1", 0) - img_info.get("x0", 0)
                        img_h = img_info.get("y1", 0) - img_info.get("y0", 0)

                        image_type = classify_image(
                            mode="pdf",
                            img_w=img_w,
                            img_h=img_h,
                            page_w=page.width,
                            page_h=page.height,
                        )

                        if image_type == ImageType.DECORATIVE:
                            document.metadata.decorative_images_filtered += 1
                            continue

                        image_node = Image(image_type=image_type)

                        image_bytes = None
                        image_ext = "png"
                        try:
                            stream = img_info.get("stream")
                            if stream and hasattr(stream, "get_data"):
                                image_bytes = stream.get_data()
                                filters = getattr(stream, "attrs", {}).get("Filter", [])
                                if isinstance(filters, str):
                                    filters = [filters]
                                if "/DCTDecode" in filters:
                                    image_ext = "jpg"
                        except Exception:
                            pass

                        if image_bytes and options.images in ("extract", "caption") and options.image_dir:
                            filename = f"{source_stem}_{page_num}_{img_idx}"
                            path = extract_image(
                                image_bytes=image_bytes,
                                ext=image_ext,
                                image_dir=Path(options.image_dir),
                                filename=filename,
                                collector=getattr(options, "collector", None),
                            )
                            if path:
                                image_node.path = path

                        # Vision captioning hookup
                        if options.images == "caption" and options.vision_provider and image_bytes:
                            try:
                                from distill.parsers._vision import caption_image
                                caption = caption_image(
                                    image_bytes=image_bytes,
                                    provider=options.vision_provider,
                                    model=options.extra.get("vision_model") or None,
                                    api_key=options.vision_api_key,
                                    base_url=options.vision_base_url,
                                )
                                if caption:
                                    image_node.caption = caption
                            except Exception as e:
                                if getattr(options, "collector", None) is not None:
                                    from distill.warnings import ConversionWarning, WarningType
                                    options.collector.add(ConversionWarning(
                                        type=WarningType.vision_caption_failed,
                                        message=f"Vision captioning failed for page {page_num} image {img_idx}: {e}",
                                    ))

                        section.blocks.append(image_node)
                except Exception:
                    pass

            # Only add section if it has content
            if section.blocks:
                document.sections.append(section)

        # Emit font encoding corruption warning if any pages were flagged
        if corrupted_pages and options.collector:
            from distill.warnings import ConversionWarning, WarningType
            options.collector.add(ConversionWarning(
                type=WarningType.FONT_ENCODING_UNSUPPORTED,
                message=(
                    f"Possible font encoding corruption detected on "
                    f"{len(corrupted_pages)} page(s). Text may contain garbled "
                    f"characters from non-Unicode custom fonts. Consider using "
                    f"OCR for affected pages."
                ),
                pages=sorted(corrupted_pages),
                count=len(corrupted_pages),
            ))

        return document
