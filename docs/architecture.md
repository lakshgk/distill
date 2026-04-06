# Distill — Architecture

## Overview

Distill converts documents (DOCX, PDF, XLSX, PPTX, Google Workspace) into
clean, LLM-optimised Markdown. The pipeline has three stages: **parse → IR →
render**.

```
  source file
      │
      ▼
  ┌─────────┐   ParseOptions (+ WarningCollector)
  │  Parser │◄──────────────────────────────────
  └────┬────┘
       │  Document (IR)
       ▼
  ┌──────────┐   render(front_matter=…)
  │ Renderer │─────────────────────────►  Markdown string
  └──────────┘
       │
       ▼
  ┌─────────┐                 ┌──────────────────┐
  │ Quality │──► QualityScore │ WarningCollector │──► structured_warnings
  └─────────┘                 └──────────────────┘
```

The registry wires format detection to the correct parser; callers use
`convert()` or `convert_to_ir()` and never touch parsers directly.

`convert()` creates a `WarningCollector` before each parse and attaches it to
`ParseOptions.collector`. Parsers call `options.collector.add(ConversionWarning(…))`
to record structured warnings. After parsing, the collector is serialised and
returned in `ConversionResult.structured_warnings`.

---

## Packages

```
distill/
  packages/
    core/          # distill-core: library + CLI
      distill/
        __init__.py      # convert(), convert_stream(), convert_to_ir(), ConversionResult
        ir.py            # IR dataclasses (Document, Section, Block, …)
        renderer.py      # MarkdownRenderer (render + render_stream)
        quality.py       # QualityScore + score()
        registry.py      # format registry / parser discovery
        warnings.py      # WarningType, ConversionWarning, WarningCollector
        parsers/
          base.py        # Parser ABC, ParseOptions, ParseError
          docx.py        # DocxParser, DocLegacyParser
          pdf.py         # PdfParser
          xlsx.py        # XlsxParser
          pptx.py        # PptxParser
          google.py      # Google Workspace (Docs / Sheets / Slides)
          _libreoffice.py  # LibreOffice bridge (.doc, .xls, .ppt)
          _ocr.py          # Scanned PDF OCR (docling + Tesseract)
          _vision.py       # Vision captioning (OpenAI / Anthropic / Ollama)
        renderers/
          __init__.py      # (empty)
          chunks.py        # ChunksRenderer — RAG-ready Chunk list
          json_renderer.py # JSONRenderer — full IR as JSON dict
          html_renderer.py # HTMLRenderer — semantic HTML string
      tests/
        test_ir.py
        test_docx.py
        test_pdf.py
        test_xlsx.py
        test_pptx.py
        test_google.py
        test_libreoffice.py
        test_ocr.py
        test_streaming.py
        test_vision.py
        test_warnings.py
        test_chunks.py
  app/             # distill-app: web UI + REST API
    distill_app/
      server.py    # FastAPI app — GET / and POST /api/convert
      static/
        index.html # Browser UI (HTML/CSS/JS, served at GET /)
    tests/
      test_server.py
```

---

## IR (Intermediate Representation)

Every parser produces a `Document` tree.  The renderer consumes the same tree
to produce Markdown.  The IR is the public contract between parsers and
renderers — changing it is a breaking change.

### Node types

| Node | Description |
|------|-------------|
| `Document` | Root node. Holds `metadata`, `sections`, `warnings`. |
| `DocumentMetadata` | Title, author, subject, description, keywords, dates, counts. |
| `Section` | A block of content introduced by a heading. `level` maps H1→1, H2→2, …, 0 = preamble. |
| `Paragraph` | A run of `TextRun` nodes. |
| `TextRun` | A span of inline text with optional bold, italic, code, strikethrough, href. |
| `Table` | Rows of `TableRow` → `TableCell`. |
| `List` | Ordered or unordered list of `ListItem`. Supports nesting. |
| `CodeBlock` | Fenced code block with optional language tag. |
| `BlockQuote` | Block-level quotation containing other blocks. |
| `Image` | Extracted image with type classification, path, alt text, caption, OCR text. |

### DocumentMetadata fields

```python
title, author, created_at, modified_at   # core provenance
subject, description, keywords           # extended properties
page_count, slide_count, sheet_count     # format-specific counts
word_count                               # estimated word count
language                                 # BCP-47 tag (e.g. "en-US")
source_format, source_path               # format of origin
```

---

## Parser Pipeline

### Registration

Parsers register themselves via `@registry.register`.  The registry maps file
extensions and MIME types to parser classes.  `registry.find(source)` selects
the best parser for a given source.

### ParseOptions

All parsers accept a `ParseOptions` dataclass:

| Field | Default | Purpose |
|-------|---------|---------|
| `images` | `"extract"` | How to handle images: extract \| suppress \| inline_ocr \| caption |
| `image_dir` | `None` | Directory for extracted image files |
| `vision_provider` | `None` | Vision model for image captioning (openai \| anthropic \| ollama) |
| `max_table_rows` | `500` | Cap rows per table to prevent memory issues |
| `include_formulas` | `False` | XLSX: render formula text vs computed value |
| `streaming` | `False` | Enable streaming output via `convert_stream()` |
| `output_format` | `"markdown"` | Output format: markdown \| chunks \| json \| html |
| `paginate_output` | `False` | Insert page separators at page boundaries (PDF/DOCX only) |
| `extra` | `{}` | Per-parser overrides (e.g. `max_file_size`, `max_unzip_size`) |
| `collector` | `None` | Injected by `convert()` — parsers use this to emit structured warnings |

### Routing

| Format | Primary | Fallback |
|--------|---------|----------|
| `.docx` | mammoth → HTML → IR | pandoc → GFM (if mammoth yields no content) |
| `.doc` | LibreOffice → .docx → DocxParser | — |
| `.odt` | LibreOffice → .docx → DocxParser | — |
| `.pdf` native | pdfplumber (text + tables) | — |
| `.pdf` scanned | docling or Tesseract (requires `[ocr]`) | — |
| `.xlsx`, `.xlsm` | openpyxl | `.xlsm` macros stripped silently |
| `.xls` | LibreOffice → .xlsx → XlsxParser | — |
| `.pptx` | python-pptx | — |
| `.ppt` | LibreOffice → .pptx → PptxParser | — |
| `.gdoc` / Drive URL | Drive API export → .docx → DocxParser | — |
| `.gsheet` / Drive URL | Drive API export → .xlsx → XlsxParser | — |
| `.gslides` / Drive URL | Drive API export → .pptx → PptxParser | — |
| `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg` | AudioParser (async only) | — |

### Audio Pipeline

Audio files are processed through a multi-stage pipeline:

```
Audio File → AudioQualityChecker → Transcriber → SpeakerDiarizer
           → IR Mapping → [TopicSegmenter — opt-in] → Document
```

**Quality pre-check:** `AudioQualityChecker` inspects file metadata (duration,
bitrate, channels, sample rate) without decoding the full audio. Emits
`AUDIO_QUALITY_LOW` warnings for low bitrate (<32 kbps), excessive duration
(>4 hours), or telephone-quality mono audio.

**Transcription:** Two engines are supported. `WhisperTranscriber` (default)
uses `faster-whisper` for high-quality transcription with word-level timestamps.
For files longer than 10 minutes, audio is split at silence boundaries before
transcription to prevent context loss. `VoskTranscriber` provides a lightweight
offline alternative using the Vosk toolkit.

Whisper model sizes (selectable in the UI or via `whisper_model` form field):

| Model | Download | CPU time (16 min file) | Quality |
|-------|----------|----------------------|---------|
| `tiny` | 39 MB | ~1-2 min | Acceptable |
| `base` | 74 MB | ~2-4 min | Good (default) |
| `small` | 244 MB | ~5-10 min | Better |
| `medium` | 769 MB | ~15-30 min | High |
| `large-v3` | 1.5 GB | ~30-60 min | Best |

Model weights are downloaded on first use and cached locally.

**Speaker diarization:** `SpeakerDiarizer` uses `pyannote.audio` to identify
speaker turns and assign labels (`Speaker A`, `Speaker B`, etc.) to each
transcription segment. Requires `DISTILL_HF_TOKEN` to download the model.

**IR mapping:**

| Audio element | IR node |
|---|---|
| Document (filename + date) | `Section(level=1)` with heading |
| Speaker turn | `Paragraph` with `**[MM:SS] Speaker X:**` prefix |
| Silence gap > 30s | New `Section(level=2)` boundary |

Audio is always processed asynchronously via the Celery worker. Synchronous
calls to `convert()` for audio input raise `ParseError` directing the caller
to use the async API. If Redis is unavailable, the API returns HTTP 503.

**Format compatibility:** The quality pre-check (`soundfile`/`librosa`) does not
support all container formats (notably `.m4a`/AAC). When metadata inspection
fails, a `AUDIO_QUALITY_LOW` warning is emitted but transcription proceeds
normally — `faster-whisper` handles `.m4a` via its own FFmpeg-based decoder.
The duration check for chunked transcription is skipped when metadata is
unavailable; the file is transcribed in a single pass instead.

**Windows workers:** On Windows, the Celery worker must use `--pool=solo`
instead of the default `prefork` pool, which has known `billiard` permission
errors on Windows:

```bash
celery -A distill_app.worker worker --loglevel=info --pool=solo -Q conversions
```

#### Topic Segmentation

After diarization, an optional LLM-powered pass groups consecutive speaker-turn
paragraphs into named topic sections. Enabled by setting
`topic_segmentation=true` in the API request plus a configured LLM key
(`llm_api_key` form field or `DISTILL_LLM_API_KEY` env var). Silently skipped
for non-audio input.

`TopicSegmenter` batches paragraphs in groups of up to 20 per LLM call. A
60-paragraph transcript produces 3 LLM calls. On any LLM failure or JSON parse
error, the document is returned without topic sections — transcription and
diarization results are never lost.

Output: the IR gains `Section(level=2)` nodes with descriptive topic headings.
These appear as `##` headings in Markdown output and as `heading_path` entries
in RAG chunks.

**Graceful degradation:**

| Missing dependency | Behaviour |
|---|---|
| Audio extras not installed | `ImportError` with install instructions |
| `DISTILL_HF_TOKEN` not set | Transcription succeeds; speaker labels omitted |
| pyannote model load failure | Warning emitted; speaker labels omitted |
| Transcription returns empty | Document with "No speech detected" paragraph |
| Metadata inspection fails (.m4a) | Warning emitted; transcription proceeds normally |
| LLM unavailable for topic seg | Transcript returned without topic sections |

### XLSX: Merged Cell Resolution

When an Excel workbook contains merged cells, `openpyxl` only stores the value in
the top-left (anchor) cell of each merge range — all other cells in the range
return `None`. The XLSX parser resolves this before row iteration:

1. **Merge map**: `_expand_merged_cells(ws)` iterates `worksheet.merged_cells.ranges`
   and builds a `dict[(row, col)] → str` mapping every coordinate in each range to
   the anchor cell's value. If the anchor value is `None`, an empty string is used.
2. **Cell lookup**: During row iteration, each `(row, col)` is checked against the
   merge map first. If present, the mapped value is used; otherwise the raw cell
   value is read as normal.
3. **All worksheets**: The merge map is rebuilt per worksheet, so the fix applies to
   every sheet in a multi-sheet workbook — not just the first.

This ensures every Markdown table row is fully self-contained, which is critical for
LLM consumption where each row must be independently interpretable.

`ParserOutcome.PARSE_ERROR` is defined in the IR enum but not yet wired to any
parser. Future parsers that encounter unrecoverable errors should set
`document.parser_outcome = ParserOutcome.PARSE_ERROR` before returning an empty
`Document`.

### OCR: Hugging Face Warning Suppression

The `docling` OCR backend loads Hugging Face models during `DocumentConverter`
initialisation. When no `HF_TOKEN` environment variable is set, the
`huggingface_hub` and `transformers` libraries emit unauthenticated-access
warnings to stderr. These warnings are harmless but would otherwise leak into
the API response and UI.

The `_suppress_hf_warnings()` context manager in `_ocr.py` handles this:

1. **stderr redirect**: `sys.stderr` is temporarily replaced with a `StringIO`
   buffer for the duration of the docling call.
2. **Line filtering**: On exit, each captured line is checked against known HF
   warning markers (`HF_TOKEN`, `huggingface`, `unauthenticated`, `rate limit`).
   Matched lines are emitted at `DEBUG` level via the `distill.parsers._ocr`
   logger — server-side only, never re-raised to stderr.
3. **Pass-through**: Non-matching lines (genuine errors) are re-emitted to the
   original stderr so real problems are never silenced.
4. **Python warnings**: `warnings.filterwarnings("ignore")` is scoped to the
   `transformers` and `huggingface_hub` module namespaces for the duration of
   the block. The original filter state is restored on exit.

Operators can set `HF_TOKEN` as a server environment variable to eliminate the
warning at source, bypassing suppression entirely.

---

## Renderers

`convert()` routes to the appropriate renderer based on `options.output_format`.
The result is stored in the corresponding field on `ConversionResult`.

| `output_format` | Renderer | `ConversionResult` field |
|-----------------|----------|--------------------------|
| `"markdown"` (default) | `MarkdownRenderer` | `.markdown` |
| `"chunks"` | `ChunksRenderer` | `.chunks` |
| `"json"` | `JSONRenderer` | `.document_json` |
| `"html"` | `HTMLRenderer` | `.html` |

The API endpoint (`POST /api/convert`) accepts `output_format` as a form field
and changes the response envelope accordingly (see [API](#api) below).

### MarkdownRenderer

Converts an IR `Document` to CommonMark / GFM Markdown.

Key behaviours:

- `front_matter=False` (default): YAML metadata block is suppressed.
- `front_matter=True`: emits a YAML front-matter block containing all non-empty
  `DocumentMetadata` fields at the top of the output.
- `paginate_output=False` (default): no page separators. When `True`, inserts
  `\n\n---\n*Page N*\n\n` at each page boundary detected in the IR via a `page`
  field on `Section` nodes. No-op until that field is added to the IR schema.
  Silently ignored for HTML and audio input.
- Images: richest available representation — `structured_data > ocr_text > caption > alt_text`.
- Tables: GFM pipe tables. Lists: CommonMark `-` / `1.` with nesting.
- Code blocks: fenced with optional language hint.

### ChunksRenderer (`distill.renderers.chunks`)

Converts an IR `Document` to a flat list of `Chunk` objects for vector-database
ingestion.

**Chunking rules:**

- Each `Section` (heading + its immediate non-table blocks) → one `"section"` chunk.
- Each `Table` block → one `"table"` chunk regardless of row count.
- Sections exceeding 800 estimated tokens are split at `Paragraph` boundaries —
  never mid-paragraph.
- Parent heading path is prepended to every child chunk's content after a split.

**`Chunk` fields:**

| Field | Type | Notes |
|-------|------|-------|
| `chunk_id` | `str` | Deterministic, derived from source document name + node position |
| `type` | `str` | `"section"` \| `"table"` \| `"list"` \| `"audio_turn"` |
| `heading_path` | `str` | Ancestor heading chain, e.g. `"Intro > Background"` |
| `content` | `str` | Markdown-rendered content of this chunk |
| `source_document` | `str` | Source file name |
| `source_format` | `str` | e.g. `"pdf"`, `"docx"` |
| `token_count` | `int` | `len(content) // 4` (approximation, no tokeniser) |
| `page_start` | `int \| None` | First page (when IR carries page metadata) |
| `page_end` | `int \| None` | Last page (when IR carries page metadata) |
| `timestamp_start` | `float \| None` | Audio only |
| `timestamp_end` | `float \| None` | Audio only |

`None` optional fields are omitted from `Chunk.to_dict()` output.

### JSONRenderer (`distill.renderers.json_renderer`)

Serialises the full IR tree to a JSON-safe `dict`.

Output shape:

```json
{
  "title":  "...",
  "format": "docx",
  "nodes":  [
    { "type": "heading", "level": 1, "content": "Introduction" },
    { "type": "paragraph", "content": "Body text." },
    { "type": "table", "headers": ["Col A"], "rows": [["1"]] },
    { "type": "list", "ordered": false, "items": [...] },
    { "type": "code", "language": "python", "content": "..." }
  ]
}
```

All optional IR fields are null-guarded; absent values are omitted (never `null`).
`title` and `format` are omitted when not set in `DocumentMetadata`.

### HTMLRenderer (`distill.renderers.html_renderer`)

Produces clean semantic HTML from the IR tree. No inline styles are emitted.

| IR node | HTML output |
|---------|-------------|
| `Section.heading` at level N | `<h1>`–`<h6>` |
| `Paragraph` | `<p>` |
| `Table` | `<table>` with `<thead>` / `<tbody>` |
| `List` (unordered) | `<ul>` / `<li>` |
| `List` (ordered) | `<ol>` / `<li>` |
| `CodeBlock` | `<pre><code>` |
| `BlockQuote` | `<blockquote>` |
| `Image` (decorative) | suppressed |
| `Image` (with OCR text) | `<pre><code>` |
| `Image` (with path) | `<img src="…" alt="…">` |

All IR field accesses are null-guarded.

---

## Quality Scoring

`distill.quality.score(ir, markdown)` returns a `QualityScore` with:

| Metric | Weight | Measures |
|--------|--------|---------|
| `heading_preservation` | 25% | Headings in IR vs headings in Markdown output |
| `table_preservation` | 25% | Tables in IR vs GFM table separators in output |
| `list_preservation` | 15% | List items in IR vs Markdown list items |
| `token_reduction_ratio` | 20% | Token efficiency of Markdown vs naive estimate |
| `valid_markdown` | 15% | Output passes CommonMark validation |

`QualityScore.passed` returns `True` if `overall >= 0.70`.

### Token Reduction Ratio

The `token_reduction_ratio` metric compares the rendered Markdown token count
against a naive baseline derived from the source document's word count
(`ir.metadata.word_count`).

**When `word_count` is available:** the metric is computed as
`1.0 - (md_words × 1.3) / (source_words × 2.5)`, clamped to `[0.0, 1.0]`.

**When `word_count` is `null`:** the metric is set to `null` in `components`
and its 20% weight is redistributed proportionally across the remaining four
metrics so the composite `overall` score still sums to 1.0:

| Metric | Normal weight | Redistributed weight |
|--------|---------------|---------------------|
| `heading_preservation` | 25% | 31.25% |
| `table_preservation` | 25% | 31.25% |
| `list_preservation` | 15% | 18.75% |
| `valid_markdown` | 15% | 18.75% |

### Source word count by parser

| Parser | `word_count` populated? | Method |
|--------|------------------------|--------|
| DOCX | Yes | `sum(len(p.text.split()) for p in doc.paragraphs)` |
| PPTX | Yes | Dedicated `_compute_word_count(prs)` helper |
| PDF (native) | Yes | `sum(len(page.extract_text().split()) for page in pages)` |
| XLSX | Yes | Sum of `len(str(cell.value).split())` across all non-None cells |
| HTML | Yes | Tag-stripped text from cleaned HTML content |
| Google Workspace | Yes | Inherited from downstream parser (DOCX/XLSX/PPTX) |
| PDF (scanned/OCR) | No | Word count unavailable before OCR — left as `null` |

---

## Warning System

`distill.warnings` provides a structured, non-fatal warning system used across the
conversion pipeline.

### Classes

| Class | Role |
|-------|------|
| `WarningType` | Enum of all warning codes (see table below) |
| `ConversionWarning` | Single warning: `type`, `message`, optional `pages`, `count` |
| `WarningCollector` | Accumulates warnings during a conversion; serialises to dicts |

### WarningType values

| Value | Emitted by | Trigger |
|-------|-----------|---------|
| `cross_page_table` | PDF parser | Adjacent table fragments detected across a page boundary |
| `math_detected` | PDF parser | Math fonts or Unicode math ranges found; conversion not enabled |
| `math_conversion_partial` | DOCX parser | OMML math present; complex structures may not round-trip cleanly |
| `scanned_content` | PDF parser | Image-only PDF detected; math/layout analysis unavailable |
| `audio_quality_low` | Audio parser | Bitrate <32 kbps, duration >4 h, or telephone-quality mono audio |
| `audio_model_missing` | Audio parser | pyannote diarization model unavailable; speaker labels omitted |
| `table_truncated` | XLSX/PDF parser | Table exceeded `max_table_rows` and was truncated |
| `content_extracted` | HTML parser | Boilerplate removal applied; some content may have been stripped |

### How warnings flow

1. `convert()` creates a `WarningCollector` and attaches it to `options.collector`.
2. Parsers call `options.collector.add(ConversionWarning(…))` during parsing.
3. After parsing, `collector.to_dict()` is called and the result is stored in
   `ConversionResult.structured_warnings` (a `list[dict]`).
4. The API response includes `warnings` as this list. If there are no warnings, `[]`
   is returned — the key is never omitted.

### ConversionWarning schema (API response shape)

```json
{
  "type":    "cross_page_table",
  "message": "Table on page 4 appears to continue onto page 5.",
  "pages":   [4, 5]
}
```

`pages` and `count` are omitted when not set.

---

## Security

All parsers apply a common set of security controls:

- **Input size limit**: 50 MB by default. Override via `options.extra['max_file_size']`.
- **Zip bomb detection** (DOCX): uncompressed ZIP content limited to 500 MB. Override via `options.extra['max_unzip_size']`.
- **XXE prevention** (DOCX): `defusedxml.ElementTree` replaces stdlib `xml.etree.ElementTree` throughout.
- **Encrypted PDF detection**: password-protected PDFs raise `ParseError` with a clear message.

---

## Public API

```python
from distill import convert, convert_stream, convert_to_ir, ConversionResult, ParseOptions

# One-call conversion — default Markdown output
result: ConversionResult = convert(
    "report.docx",
    return_ir=False,          # set True to get IR tree in result.ir
    include_metadata=False,   # emit YAML front matter; opt-in, default False
    options=ParseOptions(
        images="suppress",
        max_table_rows=200,
    ),
)
print(result.markdown)
print(result.quality_score)        # 0.0 – 1.0
print(result.metadata.title)
print(result.structured_warnings)  # list[dict] — structured warning objects

# RAG chunks output
result = convert("report.pdf", options=ParseOptions(output_format="chunks"))
for chunk in result.chunks:
    print(chunk.chunk_id, chunk.heading_path, chunk.token_count)

# JSON IR export
result = convert("report.docx", options=ParseOptions(output_format="json"))
print(result.document_json)  # {"title": ..., "nodes": [...]}

# HTML output
result = convert("report.docx", options=ParseOptions(output_format="html"))
print(result.html)  # "<h1>...</h1><p>...</p>..."

# Paginated Markdown (page separators when IR carries page metadata)
result = convert("report.pdf", options=ParseOptions(paginate_output=True))
print(result.markdown)

# Streaming conversion — yields one Markdown chunk per section
for chunk in convert_stream("report.docx"):
    print(chunk)

# With front matter as the first chunk
for chunk in convert_stream("report.docx", include_metadata=True):
    print(chunk)

# IR access
ir = convert_to_ir("report.pdf")
markdown = ir.render(front_matter=False)

# IR streaming
for chunk in ir.render_stream(front_matter=False):
    print(chunk)
```

### ConversionResult fields

| Field | Type | Populated when |
|-------|------|----------------|
| `markdown` | `str` | Always |
| `quality_score` | `float` | Always |
| `quality_details` | `QualityScore \| None` | Always |
| `metadata` | `DocumentMetadata` | Always |
| `warnings` | `list[str]` | Always |
| `structured_warnings` | `list[dict]` | Always (empty list if none) |
| `ir` | `Document \| None` | When `return_ir=True` |
| `chunks` | `list[Chunk] \| None` | When `output_format="chunks"` |
| `document_json` | `dict \| None` | When `output_format="json"` |
| `html` | `str \| None` | When `output_format="html"` |

---

## Async Job Processing

Large documents and audio files are processed asynchronously via Celery workers
backed by a Redis broker. Small files continue to use the synchronous path for
lowest latency.

### Trigger conditions

`POST /api/convert` decides sync vs async using `should_run_async()`:

| Condition | Result |
|-----------|--------|
| File size > `DISTILL_ASYNC_SIZE_THRESHOLD_MB` (default 10 MB) | Async |
| MIME type in always-async set (audio/mpeg, audio/wav, audio/mp4, audio/flac, audio/ogg, scanned_pdf) | Async |
| Redis is unhealthy | Sync (graceful degradation) |
| None of the above | Sync |

### Job lifecycle

```
  Client                        API Server              Redis           Celery Worker
    │                               │                     │                   │
    │  POST /api/convert            │                     │                   │
    │──────────────────────────────►│                     │                   │
    │                               │  set_queued(id)     │                   │
    │                               │────────────────────►│                   │
    │                               │  delay(task)        │                   │
    │                               │─────────────────────┼──────────────────►│
    │  ◄── 202 { job_id, poll_url } │                     │                   │
    │                               │                     │  set_processing   │
    │                               │                     │◄──────────────────│
    │                               │                     │                   │
    │                               │                     │  convert(file)    │
    │                               │                     │                   │
    │                               │                     │  set_complete     │
    │  GET /jobs/{id}               │                     │◄──────────────────│
    │──────────────────────────────►│  get(id)            │                   │
    │                               │────────────────────►│                   │
    │  ◄── 200 { status, result }   │                     │                   │
```

### JobStore key schema

- Pattern: `distill:job:{job_id}`
- Value: JSON-serialised `JobResult` dict
- TTL: `DISTILL_JOB_TTL_SECONDS` (default 3600), applied on every write
- States: `queued` → `processing` → `complete` | `failed`

### Redis health fallback

The API server pings Redis every 30 seconds in a background asyncio task.
When Redis is unhealthy:

- `should_run_async()` returns `False` — all requests go through the sync path
- `GET /jobs/{id}` returns HTTP 503
- If Redis becomes unhealthy between the async check and the `delay()` call,
  the API catches the error, falls back to sync, and adds a
  `X-Distill-Async: degraded` response header

### Queue routing

Async jobs are routed to one of two named Celery queues so that batch traffic
can never starve interactive requests:

| Queue | Name | Purpose |
|-------|------|---------|
| Interactive | `distill.interactive` | Human-waiting jobs — never starved by batch work |
| Batch | `distill.batch` | Background/large file processing — can wait |

**Automatic routing rules** (applied when `priority` is not supplied):

| Condition | Queue |
|-----------|-------|
| File size ≤ `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `distill.interactive` |
| File size > `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `distill.batch` |
| Audio input (any size) | `distill.batch` |
| Scanned PDF (detected at parse time) | `distill.batch` (demotion — deferred) |

**Manual override:** Callers can supply `priority=interactive` or
`priority=batch` in the `POST /api/convert` form data to override automatic
routing. Any other value returns HTTP 422.

**Worker topology:** `docker-compose.yml` runs two separate worker services
(`worker-interactive` and `worker-batch`), each consuming only their respective
queue. Concurrency is independently configurable via
`DISTILL_INTERACTIVE_CONCURRENCY` (default 4) and `DISTILL_BATCH_CONCURRENCY`
(default 2).

**Job response:** `GET /jobs/{id}` includes a `queue` field showing which queue
the job was routed to. The `queue` field is also returned in the immediate
HTTP 202 response at submission time.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for broker, backend, and job store |
| `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `10` | Files larger than this (MB) are processed async |
| `DISTILL_JOB_TTL_SECONDS` | `3600` | TTL for job results in Redis |
| `DISTILL_WORKER_CONCURRENCY` | `4` | Number of concurrent Celery worker processes |
| `DISTILL_INTERACTIVE_CONCURRENCY` | `4` | Concurrency for the interactive worker pool |
| `DISTILL_BATCH_CONCURRENCY` | `2` | Concurrency for the batch worker pool |

### Monitoring with Flower

The `docker-compose.yml` includes a [Celery Flower](https://flower.readthedocs.io/)
service at http://localhost:5555. Flower provides a real-time dashboard showing:

- Active, completed, and failed tasks
- Worker status and resource usage
- Task execution times and retry counts
- Queue lengths

For local development without Docker, Flower can be started manually:

```bash
celery -A distill_app.worker flower --port=5555
```

### Webhook Delivery

When a caller supplies a `callback_url` form field with an async job submission,
Distill POSTs the full job result to that URL when conversion completes or fails.

**Flow:**

1. Caller submits `POST /api/convert` with `callback_url=https://example.com/hook`
2. URL is validated at submission time (HTTPS only, no private IPs)
3. Job is queued and HTTP 202 returned immediately
4. Worker completes conversion and stores result in Redis
5. Worker POSTs the result to `callback_url` via `WebhookDelivery`
6. On delivery failure: retries 3 times with exponential backoff (1s, 2s, 4s)
7. If all retries fail: job status set to `callback_failed`, result preserved

**Callback payload** matches the `GET /jobs/{id}` response exactly — no new
schema. Callers who already poll can switch to webhooks with zero payload changes.

**Security controls:**
- HTTPS only — `http://` rejected with HTTP 422
- Private/reserved IP ranges rejected (SSRF protection): loopback, `10.x`,
  `172.16.x`, `192.168.x`, `169.254.x`, `localhost`
- `callback_url` is redacted in all log output
- Callback payload contents are never logged

**Known limitation:** Delivery is best-effort. If the worker crashes before
delivering the callback, the callback is not retried after restart. The job
result is always preserved in Redis regardless of callback outcome.

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTILL_WEBHOOK_TIMEOUT_SECONDS` | `10` | Timeout per delivery attempt |

### Job Progress Streaming

Every async job publishes progress events to a Redis pub/sub channel. Callers
connect to `GET /jobs/{id}/stream` and receive a real-time Server-Sent Events
(SSE) stream. The stream closes automatically when the job reaches a terminal
state.

**Flow:**

```
  Worker                          Redis pub/sub              SSE endpoint            Client
    │                                  │                         │                     │
    │  ProgressPublisher.emit()        │                         │                     │
    │─────────────────────────────────►│  PUBLISH                │                     │
    │                                  │                         │  SUBSCRIBE           │
    │                                  │────────────────────────►│                     │
    │                                  │                         │  data: {json}        │
    │                                  │                         │────────────────────►│
    │                                  │                         │                     │
    │  emit("completed", pct=100)      │                         │                     │
    │─────────────────────────────────►│────────────────────────►│  data: {completed}  │
    │                                  │                         │────────────────────►│
    │                                  │                         │  (stream closes)    │
```

**Progress event schema:**

| Field | Type | Always present | Description |
|-------|------|----------------|-------------|
| `job_id` | str | Yes | Job identifier |
| `status` | str | Yes | `queued`, `processing`, `completed`, `failed`, `callback_failed` |
| `stage` | str | No | Current pipeline stage |
| `pct` | int 0-100 | No | Estimated percentage complete |
| `queue` | str | Yes | Queue the job is running on |
| `message` | str | No | Human-readable progress description |
| `ts` | str (ISO 8601) | Yes | Event timestamp |

**Stage values and percentage milestones:**

| Stage | Formats | Approx pct |
|-------|---------|-----------|
| `queued` | All | 0 |
| `routing` | All | 2 |
| `parsing` | All doc formats | 10-80 |
| `quality_check` | All | 85 |
| `rendering` | All | 90 |
| `delivering_webhook` | When callback_url set | 95 |
| `completed` | All | 100 |
| `audio_quality_check` | Audio | 5 |
| `transcription` | Audio | 10-70 |
| `diarization` | Audio | 75-80 |
| `topic_segmentation` | Audio + LLM | 82 |
| `failed` | All | - |

**Audio instrumentation pattern:** The `ProgressPublisher` lives in `distill_app`
(app layer). Core parsers cannot import from `distill_app`. Instead, the publisher
is passed via `ParseOptions.extra["progress_publisher"]` and null-guarded at every
access. When no publisher is present (direct library use, tests), all progress
calls are silently skipped.

**Keepalive and max duration:** If no event arrives within
`DISTILL_SSE_KEEPALIVE_SECONDS` (default 15), a `: heartbeat` SSE comment is
sent to keep proxies from closing the connection. The stream auto-closes after
`DISTILL_SSE_MAX_DURATION_SECONDS` (default 3600) with a `timeout` event.

**No-op when Redis is unavailable:** `ProgressPublisher` logs once at WARNING
on first failure and stops attempting to publish for the rest of the job.
Conversion is never affected by progress publishing errors.

**Configuration:**

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTILL_SSE_KEEPALIVE_SECONDS` | `15` | Heartbeat interval for idle SSE connections |
| `DISTILL_SSE_MAX_DURATION_SECONDS` | `3600` | Maximum SSE stream duration before auto-close |

---

## Configuration Reference

All runtime configuration is centralised in `distill_app/settings.py` and read
from environment variables at startup. For local development, copy
`.env.example` to `.env` and fill in the values. For production deployments,
set these variables in your container environment or secrets manager. Never
commit `.env` to version control.

Credentials set as environment variables are never logged. The system redacts
sensitive fields (API keys, tokens, credentials paths) before any log output.

### Infrastructure

Required for all deployments.

| Environment Variable | Default | Required | Description |
|---------------------|---------|----------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Yes | Redis connection URL for broker, backend, and job store |
| `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `10` | No | Files above this size (MB) are processed asynchronously |
| `DISTILL_JOB_TTL_SECONDS` | `3600` | No | TTL (seconds) for job results in Redis |
| `DISTILL_WORKER_CONCURRENCY` | `4` | No | Celery worker process count per container |
| `DISTILL_INTERACTIVE_CONCURRENCY` | `4` | No | Concurrency for the interactive worker pool |
| `DISTILL_BATCH_CONCURRENCY` | `2` | No | Concurrency for the batch worker pool |
| `DISTILL_SSE_KEEPALIVE_SECONDS` | `15` | No | Heartbeat interval for idle SSE connections |
| `DISTILL_SSE_MAX_DURATION_SECONDS` | `3600` | No | Maximum SSE stream duration before auto-close |

### LLM Features

Required only for cross-page table merging, structured JSON extraction, and
audio topic segmentation.

| Environment Variable | Default | Required | Description |
|---------------------|---------|----------|-------------|
| `DISTILL_LLM_API_KEY` | _(empty)_ | For LLM features | API key for any OpenAI-compatible endpoint |
| `DISTILL_LLM_MODEL` | _(empty)_ | For LLM features | Model identifier (e.g. `gpt-4o`, `claude-3-5-sonnet`) |
| `DISTILL_LLM_BASE_URL` | _(empty)_ | For LLM features | Base URL for the LLM endpoint |

Per-request `llm_api_key` and `llm_model` form field values take precedence
over these server-side defaults.

### Vision / Image Captioning

Required only when `images="caption"` is set in ParseOptions.

| Environment Variable | Default | Required | Description |
|---------------------|---------|----------|-------------|
| `DISTILL_VISION_PROVIDER` | _(empty)_ | For captioning | Backend: `openai`, `anthropic`, or `ollama` |
| `DISTILL_VISION_API_KEY` | _(empty)_ | For openai/anthropic | API key for the vision provider |
| `DISTILL_VISION_BASE_URL` | _(empty)_ | For ollama | Base URL for Ollama or custom vision endpoint |

### Google Workspace

Required only for Google Docs, Sheets, and Slides input via Drive URL.

| Environment Variable | Default | Required | Description |
|---------------------|---------|----------|-------------|
| `DISTILL_GOOGLE_ACCESS_TOKEN` | _(empty)_ | Optional | Server-side default OAuth2 token |
| `DISTILL_GOOGLE_CREDENTIALS_PATH` | _(empty)_ | Optional | Path to a service account JSON key file |

Per-request `access_token` values supplied by callers always take precedence.

### Audio Pipeline

Required only when processing audio files with speaker diarization.

| Environment Variable | Default | Required | Description |
|---------------------|---------|----------|-------------|
| `DISTILL_HF_TOKEN` | _(empty)_ | For speaker labels | Hugging Face access token for pyannote model |

#### Setup steps

1. Create a free account at https://huggingface.co
2. Accept the model licence at https://huggingface.co/pyannote/speaker-diarization-3.1
3. Generate an access token at https://huggingface.co/settings/tokens
4. Set `DISTILL_HF_TOKEN` in `.env` or container environment

If `DISTILL_HF_TOKEN` is not set, speaker diarization is skipped and
transcripts are produced without speaker labels. Transcription still works.

### Feature availability matrix

| Feature | Settings group required |
|---------|------------------------|
| DOCX, PDF, XLSX, PPTX conversion | Infrastructure only |
| Google Docs / Sheets / Slides | Infrastructure + Google Workspace |
| Image captioning | Infrastructure + Vision |
| Cross-page table merging | Infrastructure + LLM features |
| Structured JSON extraction | Infrastructure + LLM features |
| Audio transcription (no speaker labels) | Infrastructure + Audio extras installed |
| Audio transcription with speaker labels | Infrastructure + Audio extras + HF_TOKEN |
| Audio topic segmentation | Infrastructure + Audio extras + HF_TOKEN + LLM features |

---

## Deployment

### Docker services

`docker-compose.yml` defines five services:

| Service | Role | Port | Health check |
|---------|------|------|-------------|
| `redis` | Redis 7 broker, backend, and job store | 6379 (internal) | `redis-cli ping` |
| `api` | FastAPI server (Web UI + REST API) | 7860 | `GET /` |
| `worker-interactive` | Celery worker for `distill.interactive` queue | — | — |
| `worker-batch` | Celery worker for `distill.batch` queue | — | — |
| `flower` | Celery Flower monitoring dashboard | 5555 | — |

### Configuration workflow

1. Copy `.env.example` to `.env`
2. Fill in values — see [Configuration reference](configuration.md) for all variables
3. `docker compose up -d`

The `REDIS_URL` inside Docker Compose uses the internal service hostname
(`redis://redis:6379/0`). For local development without Docker, use
`redis://localhost:6379/0`.

### Scaling

Scale worker services independently:

```bash
docker compose up -d --scale worker-interactive=4 --scale worker-batch=2
```

Concurrency per container is controlled by `DISTILL_INTERACTIVE_CONCURRENCY`
(default 4) and `DISTILL_BATCH_CONCURRENCY` (default 2).

