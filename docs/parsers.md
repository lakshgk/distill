# Distill — Parser Reference

This document describes how each parser works internally. It is aimed at contributors and developers who want to understand the pipeline, extend a parser, or debug a conversion.

## Libraries used

| Library | What it does |
|---------|-------------|
| **mammoth** | Converts `.docx` files to HTML, preserving headings, tables, lists, and inline formatting. Distill parses that HTML into the IR tree. |
| **python-docx** | Reads DOCX core properties (title, author, dates, page count). Used for metadata only — mammoth handles content. |
| **pdfplumber** | Extracts text and table bounding boxes from native (text-layer) PDFs. |
| **openpyxl** | Reads `.xlsx` workbooks including merged cells, formula values, and sheet metadata. |
| **python-pptx** | Reads `.pptx` presentations including slide shapes, tables, and speaker notes. |
| **docling** | IBM layout-aware document converter used for scanned PDF OCR. Understands columns, tables, and headings from page images. Requires `pip install distill-core[ocr]`. |
| **pytesseract + pdf2image** | Lightweight OCR fallback. Rasterises PDF pages and runs Tesseract on each image. Requires `pip install distill-core[ocr]`. |
| **defusedxml** | Drop-in replacement for Python's `xml.etree.ElementTree` that prevents XXE (XML External Entity) injection attacks. Used wherever Distill parses XML from untrusted files. |
| **ebooklib** | Reads `.epub` archives — used for EPUB metadata extraction. Content XHTML is parsed by the existing `HTMLParser`. Requires `pip install distill-core[epub]`. |
| **sqlparse** | Tokenises and splits SQL DDL/DML statements for the SQL schema parser. Requires `pip install distill-core[sql]`. |

**Acronyms**: OOXML = Office Open XML (the file format underlying `.docx`, `.xlsx`, `.pptx`). GFM = GitHub Flavored Markdown.

---

## DocxParser

**Module**: `distill.parsers.docx`
**Class**: `DocxParser`
**Extensions**: `.docx`
**MIME type**: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
**Required packages**: `mammoth`, `python-docx`
**Optional packages**: `pandoc` (fallback for complex documents)

### Pipeline

1. **Security checks** — input size limit (50 MB) and zip bomb limit (500 MB uncompressed).
2. **Metadata extraction** — `python-docx` reads core properties: title, author, subject, comments (→ description), keywords, created/modified dates, word count, page count (from app properties XML).
3. **Content extraction** — `mammoth` converts the `.docx` to HTML with an explicit style map (`Heading 1 => h1:fresh`) to prevent unnumbered H1 demotion from embedded style maps. `defusedxml.ElementTree` parses the HTML into the IR tree. Heading tags `h1`–`h6` create `Section` nodes; all other block elements populate the current section's `blocks`.
4. **Pandoc fallback** — if mammoth yields no content and pandoc is installed, the document is converted to GFM Markdown and wrapped in a single `Section`.

### Inline formatting

Mammoth preserves: **bold** (`<strong>`, `<b>`), *italic* (`<em>`, `<i>`), `code` (`<code>`), ~~strikethrough~~ (`<del>`, `<s>`), hyperlinks (`<a href>`). Formatting propagates recursively through nested tags.

### Tables

Tables are extracted via the HTML `<table>` element. `<thead>` rows set `is_header=True` on their cells; `<th>` cells are also treated as headers. `colspan` and `rowspan` are preserved.

### Lists

`<ul>` and `<ol>` elements are converted to IR `List` nodes. Nesting is preserved via `ListItem.children`.

### Metadata field mapping

| IR field | DOCX source |
|----------|-------------|
| `title` | `core_properties.title` |
| `author` | `core_properties.author` |
| `subject` | `core_properties.subject` |
| `description` | `core_properties.comments` (OOXML `dc:description`) |
| `keywords` | `core_properties.keywords` (split on `,` or `;`) |
| `created_at` | `core_properties.created` (ISO 8601) |
| `modified_at` | `core_properties.modified` (ISO 8601) |
| `word_count` | Sum of `len(p.text.split())` over all paragraphs |
| `page_count` | OOXML app properties `<Pages>` element |
| `source_format` | `"docx"` |

### ParseOptions support

| Option | Effect |
|--------|--------|
| `max_table_rows` | Caps rows extracted per table (default 500) |
| `extra['max_file_size']` | Override input size limit (bytes) |
| `extra['max_unzip_size']` | Override zip bomb limit (bytes) |

### Known limitations

- Tracked changes and revision marks are not preserved.
- Embedded images are represented as `Image(ImageType.UNKNOWN)` with alt text only; full image extraction is not yet implemented.

---

## DocLegacyParser

**Module**: `distill.parsers.docx`
**Class**: `DocLegacyParser`
**Extensions**: `.doc`
**MIME type**: `application/msword`
**Required packages**: `mammoth`, `python-docx`
**Requires**: LibreOffice headless (`libreoffice` or `soffice` on PATH, or `DISTILL_LIBREOFFICE` env var)

### Pipeline

1. **LibreOffice conversion** — the `.doc` file is passed to `convert_via_libreoffice()` which runs `libreoffice --headless --convert-to docx --outdir <tmpdir> <file>`. The converted `.docx` is written to an isolated temp directory.
2. **Delegation** — `DocxParser.parse()` is called on the converted file. All DocxParser behaviour (metadata, mammoth content extraction, pandoc fallback, security checks) applies unchanged.
3. **Metadata correction** — `source_format` is set to `"doc"` and `source_path` is preserved from the original input.
4. **Cleanup** — the temp directory is always removed in the `finally` block, whether or not parsing succeeds.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `extra['libreoffice_timeout']` | Override the LibreOffice subprocess timeout in seconds (default 60) |

All `DocxParser` options (`max_table_rows`, `extra['max_file_size']`, `extra['max_unzip_size']`) are also forwarded.

### LibreOffice detection

See `_libreoffice.py` — tries `DISTILL_LIBREOFFICE` env var, then `libreoffice`, `soffice`, and several well-known absolute paths on Linux, macOS, and Windows.

---

## OdtParser

**Module**: `distill.parsers.docx`
**Class**: `OdtParser`
**Extensions**: `.odt`
**MIME type**: `application/vnd.oasis.opendocument.text`
**Required packages**: `mammoth`, `python-docx`
**Requires**: LibreOffice headless

### Pipeline

1. **LibreOffice conversion** — `.odt` → `.docx` via `convert_via_libreoffice()`.
2. **Delegation** — `DocxParser.parse()` processes the converted `.docx`.
3. **Metadata correction** — `source_format` set to `"odt"`.
4. **Warning** — a `CONTENT_EXTRACTED` warning is always emitted: "ODT converted
   via LibreOffice — complex formatting may not round-trip perfectly".
5. **Cleanup** — temp directory removed in `finally` block.

Identical pipeline to `DocLegacyParser` (.doc) — LibreOffice's `--convert-to docx`
command handles both `.doc` and `.odt` transparently.

---

## PdfParser

**Module**: `distill.parsers.pdf`
**Class**: `PdfParser`
**Extensions**: `.pdf`
**MIME type**: `application/pdf`
**Required packages**: `pdfplumber`
**Optional packages**: `docling`, `pytesseract`, `pdf2image` (install with `pip install distill-core[ocr]` to enable scanned PDF support)

### Pipeline

1. **Security check** — input size limit (50 MB).
2. **Open** — `pdfplumber.open()`. Password-protected PDFs raise `ParseError` with a clear message.
3. **Native extraction** — for each page, tables are extracted first (with bounding boxes), then text is extracted from the body region (5%–90% of page height, excluding table bounding boxes). Rotated text runs are detected via character transformation matrices and corrected before paragraph splitting. Font size data from `page.chars` is used to promote large-font lines to heading `Section` nodes.
4. **Scanned PDF detection** — after native extraction, `is_scanned_pdf()` checks average word count per page. If below 5 words/page, the PDF is treated as image-only and a warning is added.
5. **OCR** — only runs if `options.extra['enable_ocr']` is `True` (default `False`). Calls `ocr_pdf()`: tries docling first, falls back to Tesseract. If neither backend is available, a `ParseError` is raised.

### Scanned PDF detection

`is_scanned_pdf(document, page_count, min_words_per_page=5.0)` computes total word count across all `Paragraph` and `Table` cell blocks, divided by page count. If below the threshold the PDF is flagged as image-only, but OCR only runs if explicitly enabled via `enable_ocr`.

### OCR backends

| Backend | Package | Quality | When used |
|---------|---------|---------|-----------|
| **docling** | `docling>=1.0` | High — layout-aware; extracts headings, tables, lists | First choice |
| **Tesseract** | `pytesseract>=0.3` + `pdf2image>=1.16` | Good | Fallback when docling unavailable |

Override backend via `options.extra['ocr_backend'] = "docling"` or `"tesseract"`.
Tesseract DPI configurable via `options.extra['ocr_dpi']` (default 300).
Tesseract language via `options.extra['ocr_lang']` (default `"eng"`).

### docling IR mapping

| docling label | Distill IR node |
|---------------|----------------|
| `title` | `Section(level=1)` |
| `section_header` | `Section(level=2)` |
| `text` / `paragraph` | `Paragraph` |
| `list_item` | `ListItem` (grouped into `List`) |
| `code` / `formula` | `CodeBlock` |
| `table` | `Table` (grid mapped to `TableRow`/`TableCell`) |
| `picture` | Suppressed in OCR path |

### Header and footer suppression

The body crop excludes the top 5% and bottom 10% of each page height. Page number lines are filtered via an extended regex that catches bare digits (`5`), pipe-prefixed (`| 6`), word-prefixed (`Page 5`), fraction-style (`5 of 20`), and dash-surrounded (`- 5 -`) patterns.

### Table extraction

Tables are extracted using `find_tables()` + per-cell `extract_words()` to prevent mid-word splits at column boundaries. Falls back to `extract_tables()` on error. Table bounding boxes are excluded from text extraction to prevent content duplication. Rows are capped at `max_table_rows` (default 500).

`_build_ir_table()` applies four false-positive filters before constructing IR nodes:

1. **All-empty** — every cell is empty (decorative boxes, logos).
2. **Majority phantom columns** — more than half the columns are entirely empty (accent bar ghost tables).
3. **Effectively single-column** — only one column has non-empty content and total text exceeds 200 chars (page borders, text-box frames). Also strips prose-only prefix rows from hybrid tables where pdfplumber merges a prose section with a real table below.
4. **Prose-in-cells** — narrow tables (<=3 cols) with average non-empty cell length >80 chars (page layout boxes with sentence-length content).

### Cross-page table detection

When a table on page N has the same column count as the first table on page N+1, a `cross_page_table` structured warning is emitted. Tracking resets when a page has no tables, preventing false positives on non-adjacent pages.

### Heading detection

`_build_line_font_map()` maps each text line's Y-coordinate to its maximum font size from `page.chars`. `_chars_to_blocks()` promotes lines with font size >= 1.4x the page median to `Section` heading nodes (2x median → H1, 1.6x → H2, else H3). Lines over 120 chars or bare page numbers are excluded. Falls back to flat `Paragraph` output when no font data is available.

### Rotated text correction

`_correct_rotated_text()` detects character runs with 90°/270° rotation via their transformation matrix (`a ≈ 0, d ≈ 0`) and reverses them to restore correct reading order.

### Font encoding corruption detection

`_detect_encoding_corruption()` checks the ratio of replacement characters, control characters, and Private Use Area codepoints in extracted text. When >8% of non-whitespace characters are corrupted, a `font_encoding_unsupported` warning is emitted.

### Metadata field mapping

| IR field | PDF metadata source |
|----------|-------------------|
| `title` | `Info['Title']` |
| `author` | `Info['Author']` |
| `subject` | `Info['Subject']` |
| `description` | `Info['Subject']` (PDF has no separate description field) |
| `keywords` | `Info['Keywords']` (split on `,` or `;`) |
| `created_at` | `Info['CreationDate']` → ISO 8601 via `_parse_pdf_date()` |
| `modified_at` | `Info['ModDate']` → ISO 8601 via `_parse_pdf_date()` |
| `page_count` | `len(pdf.pages)` |
| `source_format` | `"pdf"` |

### PDF date format

`_parse_pdf_date()` handles the PDF date string format `D:YYYYMMDDHHmmSSOHH'mm'` and converts to ISO 8601. It accepts the standard apostrophe separator (`+05'30'`), colon separator (`+05:30`), and bare `Z` for UTC.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `max_table_rows` | Caps rows extracted per table (default 500) |
| `extra['max_file_size']` | Override input size limit (bytes) |
| `extra['enable_ocr']` | Set `True` to enable OCR on scanned PDFs (default `False`) |
| `extra['ocr_backend']` | Force OCR backend: `"docling"` or `"tesseract"` |
| `extra['ocr_dpi']` | Tesseract rasterisation DPI (default 300) |
| `extra['ocr_lang']` | Tesseract language code (default `"eng"`) |

### Known limitations

- Encrypted / password-protected PDFs raise `ParseError`.
- Complex multi-column layouts may produce out-of-order text extraction (pdfplumber content-stream order).
- Non-Unicode custom font encodings produce garbled text; a `font_encoding_unsupported` warning is emitted when detected.

---

## XlsxParser

**Module**: `distill.parsers.xlsx`
**Class**: `XlsxParser`
**Extensions**: `.xlsx`, `.xlsm`, `.csv`
**MIME types**: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.ms-excel.sheet.macroEnabled.12`, `text/csv`
**Required packages**: `openpyxl`

### Pipeline

1. **Security checks** — input size limit (50 MB) and zip bomb limit (500 MB uncompressed). `.xlsx` is a ZIP archive, so both limits apply.
2. **Metadata extraction** — `wb.properties` exposes the same OOXML core-property fields as python-docx: title, creator (→ author), subject, description, keywords, created/modified dates.
3. **Sheet iteration** — each worksheet becomes an H2 `Section`. Empty and chart-only sheets (max_row == 0) are skipped with a warning.
4. **Merged cell expansion** — `ws.merged_cells.ranges` is iterated before row extraction. The top-left value of each merge range is repeated into every subordinate cell position so table rows remain fully populated.
5. **Row extraction** — `ws.iter_rows()` with the merged-cell override map applied. `data_only=True` on `load_workbook` returns cached computed values for formula cells. Cell values starting with `=` (formula strings leaked as plain text) are annotated as `[formula: =...]`. Header row datetime values are formatted as `Mon YYYY` (when day==1) or `YYYY-MM-DD`.
6. **Empty column trimming** — trailing columns that are empty across all rows are detected and stripped, preventing wide sparse tables from polluting the Markdown output.
7. **Trailing row stripping** — completely empty trailing rows (ghost rows from cleared cells) are removed before table construction.
8. **Row cap** — applied after trimming, before table construction.

### Formula caching warning

If formula cells are present but have no cached value (workbook was created programmatically and never opened in Excel), a warning is emitted per sheet. The table is still produced — formula cells render as empty strings.

### Metadata field mapping

| IR field | XLSX source |
|----------|-------------|
| `title` | `wb.properties.title` |
| `author` | `wb.properties.creator` |
| `subject` | `wb.properties.subject` |
| `description` | `wb.properties.description` |
| `keywords` | `wb.properties.keywords` (split on `,` or `;`) |
| `created_at` | `wb.properties.created` (datetime → ISO 8601) |
| `modified_at` | `wb.properties.modified` (datetime → ISO 8601) |
| `sheet_count` | `len(wb.sheetnames)` |
| `source_format` | `"xlsx"` or `"csv"` |

### XLSM support

`.xlsm` (macro-enabled Excel workbook) is handled identically to `.xlsx`.
`openpyxl` reads the data and sheet structure; macros stored in the ZIP archive
are never executed and are silently ignored. A `CONTENT_EXTRACTED` warning is
always emitted for `.xlsm` input: "XLSM macro-enabled workbook: macros are not
executed and have been stripped. Data and sheet structure are preserved."

### CSV path

When the source file has extension `.csv`, the stdlib `csv` module is used instead of openpyxl. Metadata is minimal (`source_format`, `source_path`). First row is treated as header. Trailing empty columns are trimmed. Row cap applies.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `max_table_rows` | Caps rows extracted per sheet (default 500; 0 = unlimited) |
| `extra['max_file_size']` | Override input size limit (bytes) |
| `extra['max_unzip_size']` | Override zip bomb limit (bytes) |

### Known limitations

- Image and chart extraction from sheets is not yet implemented.
- Multi-level header detection (multiple header rows) is not yet implemented.

---

## XlsLegacyParser

**Module**: `distill.parsers.xlsx`
**Class**: `XlsLegacyParser`
**Extensions**: `.xls`
**MIME type**: `application/vnd.ms-excel`
**Required packages**: `openpyxl`
**Requires**: LibreOffice headless (`libreoffice` or `soffice` on PATH, or `DISTILL_LIBREOFFICE` env var)

### Pipeline

1. **LibreOffice conversion** — converts `.xls` to `.xlsx` via `convert_via_libreoffice(..., "xlsx")`.
2. **Delegation** — `XlsxParser.parse()` is called on the converted file.
3. **Metadata correction** — `source_format` is set to `"xls"` and `source_path` is preserved.
4. **Cleanup** — temp directory always removed in `finally` block.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `extra['libreoffice_timeout']` | Override the LibreOffice subprocess timeout (default 60s) |

All `XlsxParser` options are also forwarded.

---

## PptxParser

**Module**: `distill.parsers.pptx`
**Class**: `PptxParser`
**Extensions**: `.pptx`
**MIME type**: `application/vnd.openxmlformats-officedocument.presentationml.presentation`
**Required packages**: `python-pptx`

### Pipeline

1. **Security checks** — input size limit (50 MB) and zip bomb limit (500 MB uncompressed). `.pptx` is a ZIP archive.
2. **Metadata extraction** — `prs.core_properties` exposes the same OOXML core-property fields as python-docx.
3. **Word count** — summed across all slide text frames and speaker notes.
4. **Slide iteration** — each slide becomes an H2 `Section`. Title extraction uses the standard `slide.shapes.title` placeholder first, then a position + font-size heuristic fallback for custom-layout decks (top 15% of slide, font >= 20pt). Title shapes and footer placeholders (idx 11/12/13) are skipped in body processing. Bullet text frames are excluded from title fallback candidates.
5. **Text frame parsing** — bullet detection checks `<a:buChar>` and `<a:buAutoNum>` inside `<a:pPr>` (not directly under `<a:p>`), respects `<a:buNone>` suppression, and treats `para.level > 0` as a bullet. Consecutive bullet paragraphs are grouped into nested `List` IR nodes via `_build_list_from_flat()`. Non-bullet paragraphs produce `Paragraph` nodes.
6. **Tables** — `shape.has_table` shapes are converted to IR `Table`. First row is treated as header. `max_table_rows` cap applies.
7. **Speaker notes** — `slide.notes_slide.notes_text_frame.text` is appended as a `BlockQuote` at the end of each section, if non-empty.
8. **Image alt text** — `shape.description` (OOXML `descr` attribute, author-provided alt text) is preferred over `shape.name` (internal shape name). Falls back gracefully when `description` is not available.

### Metadata field mapping

| IR field | PPTX source |
|----------|-------------|
| `title` | `core_properties.title` |
| `author` | `core_properties.author` |
| `subject` | `core_properties.subject` |
| `description` | `core_properties.description` |
| `keywords` | `core_properties.keywords` (split on `,` or `;`) |
| `created_at` | `core_properties.created` (datetime → ISO 8601) |
| `modified_at` | `core_properties.modified` (datetime → ISO 8601) |
| `slide_count` | `len(prs.slides)` |
| `word_count` | Sum of all shape and notes text word counts |
| `source_format` | `"pptx"` |

### ParseOptions support

| Option | Effect |
|--------|--------|
| `max_table_rows` | Caps rows extracted per table (default 500) |
| `extra['max_file_size']` | Override input size limit (bytes) |
| `extra['max_unzip_size']` | Override zip bomb limit (bytes) |

### Known limitations

- Animations, transitions, and slide master content are not extracted.
- SmartArt is not yet extracted.

---

## PptLegacyParser

**Module**: `distill.parsers.pptx`
**Class**: `PptLegacyParser`
**Extensions**: `.ppt`
**MIME type**: `application/vnd.ms-powerpoint`
**Required packages**: `python-pptx`
**Requires**: LibreOffice headless (`libreoffice` or `soffice` on PATH, or `DISTILL_LIBREOFFICE` env var)

### Pipeline

1. **LibreOffice conversion** — converts `.ppt` to `.pptx` via `convert_via_libreoffice(..., "pptx")`.
2. **Delegation** — `PptxParser.parse()` is called on the converted file.
3. **Metadata correction** — `source_format` is set to `"ppt"` and `source_path` is preserved.
4. **Cleanup** — temp directory always removed in `finally` block.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `extra['libreoffice_timeout']` | Override the LibreOffice subprocess timeout (default 60s) |

All `PptxParser` options are also forwarded.

---

## GoogleDocsParser

**Module**: `distill.parsers.google`
**Class**: `GoogleDocsParser`
**Extensions**: `.gdoc`
**MIME type**: `application/vnd.google-apps.document`
**Required packages**: `google-api-python-client`, `google-auth`
**Install**: `pip install distill-core[google]`

### Input formats

| Input | Handled as |
|-------|-----------|
| Drive edit/share URL (`https://docs.google.com/document/d/<ID>/...`) | File ID extracted from `/d/<ID>` segment |
| Bare file ID string (25–50 alphanumeric/dash/underscore chars) | Used directly |
| Local `.gdoc` shortcut file path | File stem used as file ID |

Bytes input raises `ParseError`.

### Pipeline

1. **File ID extraction** — `_extract_file_id()` parses the URL or ID from `source`.
2. **Credential resolution** — `_build_credentials()` resolves auth from `options.extra`, env var, or raises.
3. **Drive metadata fetch** — `files().get(fileId=..., fields="mimeType,name")` confirms the file is a Google Doc and fetches its display name.
4. **Export** — `files().export_media(mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")` downloads the `.docx` representation.
5. **Delegation** — `DocxParser.parse(bytes, options)` handles all content extraction.
6. **Metadata annotation** — `source_format = "google-docs"`, `source_path = <original source>`, `title` set from Drive file name (if not already populated by DocxParser).

### Authentication

Credentials are resolved in order:

1. `options.extra['google_credentials']` — a pre-built `google.oauth2.credentials.Credentials` / `google.oauth2.service_account.Credentials` object, **or** a path string to a service account JSON key file.
2. `options.extra['access_token']` — a raw OAuth2 access token string.
3. `DISTILL_GOOGLE_CREDENTIALS` env var — path to a service account JSON key file.

### ParseOptions support

All `DocxParser` options are forwarded to the delegate parser.

### Error behaviour

| Condition | Exception |
|-----------|-----------|
| No credentials found | `ParseError` |
| File ID cannot be extracted | `ParseError` |
| Drive returns 403 | `ParseError` ("Permission denied") |
| Drive returns 404 | `ParseError` ("not found") |
| File is not a Google Doc | `UnsupportedFormatError` |
| `google-api-python-client` not installed | `ParseError` (with install hint) |

---

## GoogleSheetsParser

**Module**: `distill.parsers.google`
**Class**: `GoogleSheetsParser`
**Extensions**: `.gsheet`
**MIME type**: `application/vnd.google-apps.spreadsheet`
**Required packages**: `google-api-python-client`, `google-auth`
**Install**: `pip install distill-core[google]`

Same pipeline as `GoogleDocsParser` but:
- Expects a Google Sheets MIME type; raises `UnsupportedFormatError` otherwise.
- Exports as `.xlsx` and delegates to `XlsxParser`.
- Sets `source_format = "google-sheets"`.

---

## GoogleSlidesParser

**Module**: `distill.parsers.google`
**Class**: `GoogleSlidesParser`
**Extensions**: `.gslides`
**MIME type**: `application/vnd.google-apps.presentation`
**Required packages**: `google-api-python-client`, `google-auth`
**Install**: `pip install distill-core[google]`

Same pipeline as `GoogleDocsParser` but:
- Expects a Google Slides MIME type; raises `UnsupportedFormatError` otherwise.
- Exports as `.pptx` and delegates to `PptxParser`.
- Sets `source_format = "google-slides"`.

---

## Vision Captioning

**Module**: `distill.parsers._vision`
**Install**: `pip install distill-core[vision]`
**Entry point**: `caption_images(doc, options)`

### Overview

Vision captioning is an optional post-parse step that replaces `Image` placeholders
with LLM-generated descriptions. It is called automatically by `convert()` and
`convert_stream()` when both conditions are met:

1. `options.images == "caption"`
2. `options.vision_provider` is set to a supported provider

`distill-core` remains LLM-free by default. Provider SDKs are imported lazily inside
`caption_images()`. If `distill-core[vision]` is not installed, one warning is appended
to `doc.warnings` and the function returns silently — no error, no surprise API calls.

### Provider support

| Provider | Default model | Override via |
|----------|--------------|--------------|
| `"openai"` | `gpt-4o` | `options.extra['vision_model']` |
| `"anthropic"` | `claude-3-5-haiku-latest` | `options.extra['vision_model']` |
| `"ollama"` | `llava` | `options.extra['vision_model']` |

Ollama base URL defaults to `http://localhost:11434`. Override via
`options.extra['vision_base_url']`.

### Skip rules

Images are skipped silently (no warning) when:

- `image.path` is `None` — no file was extracted to disk
- `image.image_type == ImageType.DECORATIVE`
- `image.caption` is already populated

### Credentials

| Source | Key |
|--------|-----|
| `ParseOptions.vision_api_key` | Primary — passed directly to the provider client |
| `options.extra['openai_api_key']` | OpenAI fallback |
| `options.extra['anthropic_api_key']` | Anthropic fallback |
| Environment variable | Provider SDK default (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) |

Credentials are never read from `distill-core` internals.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `images` | Must be `"caption"` to enable vision captioning |
| `vision_provider` | `"openai"`, `"anthropic"`, or `"ollama"` |
| `vision_api_key` | API key passed to the provider client |
| `extra['vision_model']` | Override the default model for the selected provider |
| `extra['vision_base_url']` | Ollama only — base URL (default `http://localhost:11434`) |

### Example

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert(
    "report.docx",
    options=ParseOptions(
        images="caption",
        vision_provider="openai",
        vision_api_key="sk-...",
    ),
)
```

---

## HTMLParser

**Module**: `distill.parsers.html`
**Class**: `HTMLParser`
**Extensions**: `.html`, `.htm`
**MIME type**: `text/html`
**Required packages**: none (stdlib `html.parser` is sufficient)
**Optional packages**: `trafilatura`, `readability-lxml` (install with `pip install distill-core[html]` to enable boilerplate removal)

### Pipeline

1. **Source loading** — reads bytes, file path, or raw string. Bytes are decoded as UTF-8 (with `errors="replace"`).
2. **Content extraction** (opt-in) — when `options.extra['extract_content']` is `True`, `HTMLContentExtractor` strips navigation, footers, and ads using trafilatura first, then readability-lxml as fallback. If both fail, raw HTML is used and a `content_extracted` warning is emitted.
3. **DOM parsing** — lxml is used if installed; stdlib `ElementTree` with entity normalisation is the fallback.
4. **IR mapping** — the DOM is walked depth-first. Heading tags create `Section` nodes; all other block elements populate the current section's `blocks`. Unknown tags have their text extracted as `Paragraph` nodes — the parser never raises on unexpected input.

### Tag mapping

| HTML tag | IR node |
|----------|---------|
| `h1`–`h6` | `Section` (level 1–6) |
| `p` | `Paragraph` |
| `ul` / `ol` | `List` (ordered/unordered, up to 3 levels of nesting) |
| `table` | `Table` (`<thead>` or first row → header cells) |
| `pre` / `code` | `CodeBlock` |
| `img` | `Image(ImageType.UNKNOWN, alt_text=…)` |
| `div`, `article`, `main`, `section`, `body`, `html` | transparent — children are processed recursively |
| `script`, `style`, `meta`, `link`, `head` | suppressed |
| all other tags | text extracted as `Paragraph` |

### Inline formatting

`<strong>`/`<b>` → `bold`, `<em>`/`<i>` → `italic`, `<code>` → `code`, `<del>`/`<s>`/`<strike>` → `strikethrough`, `<a href>` → `href`. Formatting propagates recursively through nested tags.

### Table header detection

If a `<thead>` element is present, its cells are marked `is_header=True`. If no `<thead>` is present, the first row's cells are treated as headers.

### Content extraction flag

| `extra['extract_content']` | Behaviour |
|---------------------------|-----------|
| `False` (default) | Raw HTML is parsed as-is |
| `True` | trafilatura → readability-lxml → raw HTML fallback |

Set via `ParseOptions(extract_content=True)` (stored in `ParseOptions.extract_content`) or via `ParseOptions(extra={"extract_content": True})`. The parser reads from `options.extra`.

### Warnings emitted

| Warning | Trigger |
|---------|---------|
| `content_extracted` | `extract_content=True` and both trafilatura and readability-lxml failed or are not installed |

### Page behaviour

HTML input has no concept of pages. `page_count` is not set in `DocumentMetadata`. `paginate_output` is silently ignored.

### Metadata

| IR field | Value |
|----------|-------|
| `source_format` | `"html"` |
| all other fields | not populated (HTML has no standard metadata) |

### ParseOptions support

| Option | Effect |
|--------|--------|
| `extra['extract_content']` | `True` to strip boilerplate before parsing (default `False`) |

### Known limitations

- `<meta>` tag metadata (title, author, description) is not extracted.
- Image `src` paths are not resolved; `Image.path` is always `None`.
- Streaming (`convert_stream`) is not supported for HTML input.

---

## AudioParser

Transcribes audio files and produces structured Markdown with timestamps and
optional speaker labels.

### Supported formats

| Extension | MIME type |
|-----------|----------|
| `.mp3` | `audio/mpeg` |
| `.wav` | `audio/wav` |
| `.m4a` | `audio/mp4` |
| `.flac` | `audio/flac` |
| `.ogg` | `audio/ogg` |

### Installation

```bash
pip install distill-core[audio]
```

This installs `faster-whisper`, `vosk`, `pyannote.audio`, `librosa`,
`soundfile`, and `pydub`.

### Hugging Face setup (for speaker diarization)

1. Create a free account at https://huggingface.co
2. Accept the model licence at https://huggingface.co/pyannote/speaker-diarization-3.1
3. Generate an access token at https://huggingface.co/settings/tokens
4. Set `DISTILL_HF_TOKEN` in `.env` or container environment

### ParseOptions fields

| Field | Default | Description |
|-------|---------|-------------|
| `transcription_engine` | `"whisper"` | Transcription backend: `whisper` or `vosk` |
| `whisper_model` | `"base"` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `hf_token` | `None` | Hugging Face token for pyannote (per-request override) |

### Format compatibility

`soundfile`/`librosa` cannot inspect metadata for all container formats
(notably `.m4a`/AAC). When metadata inspection fails, an `AUDIO_QUALITY_LOW`
warning is emitted but transcription proceeds normally — `faster-whisper`
handles `.m4a` natively via its FFmpeg-based audio decoder.

The UI exposes a **Whisper Model** dropdown (visible only for audio files)
allowing users to choose between speed and quality. The `base` model is
selected by default.

### Warnings emitted

| Warning type | Condition |
|-------------|-----------|
| `AUDIO_QUALITY_LOW` | Bitrate <32 kbps, duration >4 hours, telephone-quality audio, or metadata unreadable |
| `AUDIO_MODEL_MISSING` | pyannote model unavailable; speaker labels omitted |

### Output format support

Audio input supports `markdown` and `chunks` output formats only. Requests
with `output_format=json` or `output_format=html` return HTTP 422.

### Topic Segmentation

After transcription and diarization, an optional LLM pass groups speaker turns
into named topic sections.

**How to enable:** set `topic_segmentation=true` form field along with
`llm_api_key` and `llm_model` (or configure `DISTILL_LLM_API_KEY` and
`DISTILL_LLM_MODEL` as env vars).

**What changes in the output:** `##` topic headings group the transcript into
semantic sections. Each topic section becomes a chunk with the topic name in
`heading_path` when using `output_format=chunks`.

**Fallback:** if the LLM is unavailable or returns unparseable responses, the
transcript is returned without topic sections. Transcription and diarization
results are never lost.

### Async requirement

Audio is always processed asynchronously via the Celery worker queue. If Redis
is unavailable, the API returns HTTP 503. Audio files are never processed
synchronously regardless of file size.

On Windows, start the Celery worker with `--pool=solo` to avoid `billiard`
permission errors:

```bash
celery -A distill_app.worker worker --loglevel=info --pool=solo -Q conversions
```

---

## EPUBParser

**Module**: `distill.parsers.epub`
**Class**: `EPUBParser`
**Extensions**: `.epub`
**MIME type**: `application/epub+zip`
**Required packages**: `ebooklib >= 0.18`

### Installation

```bash
pip install "distill-core[epub]"
```

### How it works

An `.epub` file is a ZIP archive containing XHTML content files, a CSS
stylesheet, images, and an OPF manifest (`content.opf`) that defines the
reading order.

1. The parser opens the ZIP and reads `META-INF/container.xml` (via
   `defusedxml`) to locate the OPF manifest file.
2. The OPF manifest provides Dublin Core metadata (title, author, language)
   and a spine element that defines the reading order of content files.
3. Each spine item (XHTML file) is extracted from the ZIP and passed through
   `HTMLParser.parse()`, which handles headings, tables, lists, and
   inline formatting.
4. Sections from all spine items are appended to a master `Document` in
   spine order.

### IR mapping

| EPUB element | IR mapping |
|---|---|
| OPF `<dc:title>` | `DocumentMetadata.title` |
| OPF `<dc:creator>` | `DocumentMetadata.author` |
| OPF `<dc:language>` | `DocumentMetadata.language` |
| Each spine item (XHTML file) | Routed through `HTMLParser.parse()` |
| Images | Alt text preserved; image files not extracted |

### Word count

Sum of word counts across all spine items (computed by `HTMLParser`).

---

## WSDLParser

**Module**: `distill.parsers.wsdl`
**Class**: `WSDLParser`
**Extensions**: `.wsdl`, `.wsd`
**MIME type**: `application/wsdl+xml`
**Required packages**: None — uses `defusedxml` (already in stack)

### How it works

WSDL (Web Services Description Language) files are XML documents describing
SOAP web services. The parser handles both WSDL 1.1
(`http://schemas.xmlsoap.org/wsdl/`) and WSDL 2.0
(`http://www.w3.org/ns/wsdl`) by detecting the root element namespace.

Parse order: types, messages, portTypes/interfaces, bindings, services.

### IR mapping

| WSDL element | IR mapping |
|---|---|
| `<wsdl:service>` | Section level 1 |
| `<wsdl:portType>` | Section level 2 |
| `<wsdl:operation>` | Section level 3 |
| `<wsdl:input>` / `<wsdl:output>` | Paragraph (message name and type) |
| `<wsdl:message>` parts | Table (part name, type columns) |
| `<wsdl:types>` XSD elements | Section level 2 with Table of fields |
| `<wsdl:documentation>` | Paragraph |
| `<wsdl:binding>` | Section level 2 |

### Word count

Sum of all visible text content across documentation and type definition
elements, split by whitespace.

---

## JSONParser

**Module**: `distill.parsers.json_parser`
**Class**: `JSONParser`
**Extensions**: `.json`
**MIME type**: `application/json`
**Required packages**: None — stdlib `json` only

### How it works

The parser auto-detects the JSON structure and routes to the appropriate
renderer:

| Type | Detection | Rendering |
|------|-----------|-----------|
| **JSON Schema** | `$schema` key, or `properties` + `type`, or `$defs`/`definitions` | Sections + property Tables |
| **API dump** | Array of dicts (all items are dicts) | Single Table (keys as headers) |
| **Flat object** | Dict with only scalar values | Two-column key/value Table |
| **Code fallback** | Everything else | Fenced `json` CodeBlock |

Schema rendering extracts `title` and `description` into metadata. Each
`properties` object becomes a Table with name, type, required, and
description columns. `$defs`/`definitions` entries become level-2 Sections.
Max recursion depth is 4.

Array dumps are capped at `max_table_rows` (default 500). A
`TABLE_TRUNCATED` warning is emitted when truncated.

### Word count

Sum of all string leaf values in the JSON structure, split by whitespace.

---

## SQLParser

**Module**: `distill.parsers.sql`
**Class**: `SQLParser`
**Extensions**: `.sql`
**MIME type**: `application/sql`
**Required packages**: `sqlparse >= 0.5`

### Installation

```bash
pip install "distill-core[sql]"
```

### How it works

The parser uses `sqlparse` to tokenise and split SQL into individual
statements. DDL statements (`CREATE TABLE`, `CREATE VIEW`, etc.) are
rendered as structured Markdown. DML statements (`SELECT`, `INSERT`, etc.)
are rendered as fenced SQL code blocks.

**The parser does not execute any SQL.**

### IR mapping

| SQL element | IR mapping |
|---|---|
| `CREATE TABLE name` | Section level 1 with table name heading |
| Column definitions | Table (name, type, constraints, nullable, default) |
| `PRIMARY KEY` constraint | Noted in constraints column |
| `FOREIGN KEY` constraint | Paragraph below column table |
| `CREATE INDEX name ON table` | Section level 2 under the referenced table |
| `CREATE VIEW name` | Section level 1 with CodeBlock of view body |
| `CREATE PROCEDURE/FUNCTION` | Section level 1 with CodeBlock of body |
| `-- comments` above CREATE | Paragraph prepended as description |
| DML statements | CodeBlock with `sql` language hint |

### Word count

Sum of table names, column names, and comment text, split by whitespace.
