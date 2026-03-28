# Distill — Parser Reference

## DocxParser

**Module**: `distill.parsers.docx`
**Class**: `DocxParser`
**Extensions**: `.docx`
**MIME type**: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
**Required packages**: `mammoth`, `python-docx` (`docx`)
**Optional packages**: `pandoc` (fallback for complex documents)

### Pipeline

1. **Security checks** — input size limit (50 MB) and zip bomb limit (500 MB uncompressed).
2. **Metadata extraction** — `python-docx` reads core properties: title, author, subject, comments (→ description), keywords, created/modified dates, word count, page count (from app properties XML).
3. **Content extraction** — `mammoth` converts `.docx` to HTML.  `defusedxml.ElementTree` parses the HTML into the IR tree.  Heading tags `h1`–`h6` create `Section` nodes; all other block elements populate the current section's `blocks`.
4. **Pandoc fallback** — if mammoth yields no content and pandoc is installed, the document is converted to GFM Markdown and wrapped in a single `Section`.

### Inline formatting

Mammoth preserves: **bold** (`<strong>`, `<b>`), *italic* (`<em>`, `<i>`), `code` (`<code>`), ~~strikethrough~~ (`<del>`, `<s>`), hyperlinks (`<a href>`).  Formatting propagates recursively through nested tags.

### Tables

Tables are extracted via the HTML `<table>` element.  `<thead>` rows set `is_header=True` on their cells; `<th>` cells are also treated as headers.  `colspan` and `rowspan` are preserved.

### Lists

`<ul>` and `<ol>` elements are converted to IR `List` nodes.  Nesting is preserved via `ListItem.children`.

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

- `.doc` (legacy binary Word) requires LibreOffice — not yet implemented (Phase 2).
- Embedded images are represented as `Image(ImageType.UNKNOWN)` with alt text only; full image extraction is Phase 2.
- Tracked changes and revision marks are not preserved.

---

## DocLegacyParser

**Module**: `distill.parsers.docx`
**Class**: `DocLegacyParser`
**Extensions**: `.doc`
**MIME type**: `application/msword`
**Required packages**: `mammoth`, `python-docx` (`docx`)
**Requires**: LibreOffice headless (`libreoffice` or `soffice` on PATH, or `DISTILL_LIBREOFFICE` env var)

### Pipeline

1. **LibreOffice conversion** — the `.doc` file is passed to `convert_via_libreoffice()` which runs `libreoffice --headless --convert-to docx --outdir <tmpdir> <file>`.  The converted `.docx` is written to an isolated temp directory.
2. **Delegation** — `DocxParser.parse()` is called on the converted file.  All DocxParser behaviour (metadata, mammoth content extraction, pandoc fallback, security checks) applies unchanged.
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

## PdfParser

**Module**: `distill.parsers.pdf`
**Class**: `PdfParser`
**Extensions**: `.pdf`
**MIME type**: `application/pdf`
**Required packages**: `pdfplumber`
**Optional packages**: `docling`, `pytesseract`, `pdf2image` (install with `pip install distill-core[ocr]` to enable scanned PDF support)

### Pipeline

1. **Security check** — input size limit (50 MB).
2. **Open** — `pdfplumber.open()`.  Password-protected PDFs raise `ParseError` with a clear message.
3. **Native extraction** — for each page, tables are extracted first (with bounding boxes), then text is extracted from the body region (5%–92% of page height, excluding table bounding boxes).
4. **Scanned PDF quality gate** — after native extraction, `is_scanned_pdf()` checks average word count per page.  If below 5 words/page, the PDF is treated as image-only.
5. **OCR** — `ocr_pdf()` is called: tries docling first, falls back to Tesseract.  If neither backend is available, a warning is appended and the sparse native result is returned.

### Scanned PDF detection

`is_scanned_pdf(document, page_count, min_words_per_page=5.0)` computes total word count across all `Paragraph` blocks divided by page count.  The threshold can be tuned via subclassing or by modifying `options.extra['min_words_per_page']` (future option — currently hardcoded at 5.0).

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

The body crop excludes the top 5% and bottom 8% of each page height.  Lines matching `^\s*\d+\s*$` (bare page numbers) are also filtered out.

### Table extraction

`pdfplumber` detects table bounding boxes before text extraction.  Tables are extracted first; their regions are then excluded from text extraction to prevent content duplication.  Rows are capped at `max_table_rows` (default 500).

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

`_parse_pdf_date()` handles the PDF date string format `D:YYYYMMDDHHmmSSOHH'mm'` and converts to ISO 8601.  It accepts the standard apostrophe separator (`+05'30'`), colon separator (`+05:30`), and bare `Z` for UTC.

### ParseOptions support

| Option | Effect |
|--------|--------|
| `max_table_rows` | Caps rows extracted per table (default 500) |
| `extra['max_file_size']` | Override input size limit (bytes) |
| `extra['ocr_backend']` | Force OCR backend: `"docling"` or `"tesseract"` |
| `extra['ocr_dpi']` | Tesseract rasterisation DPI (default 300) |
| `extra['ocr_lang']` | Tesseract language code (default `"eng"`) |

### Known limitations

- Encrypted / password-protected PDFs raise `ParseError`.
- Complex multi-column layouts may produce out-of-order text extraction.
- Image extraction is not yet implemented (Phase 2).

---

---

## XlsxParser

**Module**: `distill.parsers.xlsx`
**Class**: `XlsxParser`
**Extensions**: `.xlsx`, `.csv`
**MIME types**: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `text/csv`
**Required packages**: `openpyxl`

### Pipeline

1. **Security checks** — input size limit (50 MB) and zip bomb limit (500 MB uncompressed). `.xlsx` is a ZIP archive, so both limits apply.
2. **Metadata extraction** — `wb.properties` exposes the same OOXML core-property fields as python-docx: title, creator (→ author), subject, description, keywords, created/modified dates.
3. **Sheet iteration** — each worksheet becomes an H2 `Section`. Empty and chart-only sheets (max_row == 0) are skipped with a warning.
4. **Merged cell expansion** — `ws.merged_cells.ranges` is iterated before row extraction. The top-left value of each merge range is repeated into every subordinate cell position so table rows remain fully populated.
5. **Row extraction** — `ws.iter_rows()` with the merged-cell override map applied. `data_only=True` on `load_workbook` returns cached computed values for formula cells.
6. **Empty column trimming** — trailing columns that are empty across all rows are detected and stripped, preventing wide sparse tables from polluting the Markdown output.
7. **Row cap** — applied after trimming, before table construction.

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

---

## PptxParser

**Module**: `distill.parsers.pptx`
**Class**: `PptxParser`
**Extensions**: `.pptx`
**MIME type**: `application/vnd.openxmlformats-officedocument.presentationml.presentation`
**Required packages**: `pptx` (python-pptx)

### Pipeline

1. **Security checks** — input size limit (50 MB) and zip bomb limit (500 MB uncompressed). `.pptx` is a ZIP archive.
2. **Metadata extraction** — `prs.core_properties` exposes the same OOXML core-property fields as python-docx.
3. **Word count** — summed across all slide text frames and speaker notes.
4. **Slide iteration** — each slide becomes an H2 `Section`. The slide title shape text is placed in the section heading; all other shapes are placed in section blocks.
5. **Text frame parsing** — paragraph `level > 0` or presence of `a:buChar`/`a:buAutoNum` XML markers causes the paragraph to be treated as a `ListItem`; otherwise it becomes a `Paragraph`. Inline bold and italic formatting is preserved from run font properties.
6. **Tables** — `shape.has_table` shapes are converted to IR `Table`. First row is treated as header. `max_table_rows` cap applies.
7. **Speaker notes** — `slide.notes_slide.notes_text_frame.text` is appended as a `BlockQuote` at the end of each section, if non-empty.

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

- Image extraction not yet implemented; picture shapes produce `Image(ImageType.UNKNOWN)` with shape name as alt text.
- Animations, transitions, and slide master content are not extracted.
- SmartArt is not yet extracted.

---

## PptLegacyParser

**Module**: `distill.parsers.pptx`
**Class**: `PptLegacyParser`
**Extensions**: `.ppt`
**MIME type**: `application/vnd.ms-powerpoint`
**Required packages**: `pptx` (python-pptx)
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
**Required packages**: `google-api-python-client` (`googleapiclient`), `google-auth` (`google.auth`)
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

`distill-core` remains LLM-free. Provider SDKs are imported lazily inside
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

## Planned parsers (upcoming phases)

| Parser | Phase | Notes |
|--------|-------|-------|
