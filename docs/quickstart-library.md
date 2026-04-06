# Using Distill as a Python library

Distill-core is a standalone Python library that converts enterprise document
formats into clean, LLM-optimised CommonMark Markdown. No server, no Docker,
no background workers -- just `pip install` and call `convert()`.

---

## Install

**Core formats** (DOCX, XLSX, PPTX, PDF, HTML, JSON, WSDL):

```bash
pip install distill-core
```

**Optional extras** -- install only what you need:

```bash
pip install "distill-core[ocr]"      # scanned PDF via Tesseract + Docling
pip install "distill-core[audio]"    # audio transcription (Whisper, Vosk, pyannote)
pip install "distill-core[epub]"     # EPUB parsing
pip install "distill-core[sql]"      # SQL schema parsing
pip install "distill-core[google]"   # Google Workspace (Docs, Sheets, Slides)
pip install "distill-core[vision]"   # AI-powered image captioning
pip install "distill-core[html]"     # HTML content extraction (trafilatura)
```

Install multiple extras at once:

```bash
pip install "distill-core[ocr,vision,audio]"
```

---

## Basic usage

```python
from distill import convert

result = convert("report.docx")

print(result.markdown)        # clean CommonMark output
print(result.quality_score)   # 0.0 - 1.0 composite score
print(result.warnings)        # list[str] — human-readable warnings
```

`convert()` accepts a file path (string or `pathlib.Path`) or raw `bytes`:

```python
from pathlib import Path

# Path object
result = convert(Path("data/invoice.pdf"))

# Raw bytes
with open("report.docx", "rb") as f:
    result = convert(f.read())
```

### Include document metadata

Pass `include_metadata=True` to prepend a YAML front-matter block:

```python
result = convert("report.docx", include_metadata=True)
print(result.markdown)
# ---
# title: Quarterly Report
# author: Jane Doe
# ...
# ---
# (markdown body follows)
```

### Keep the IR tree

Pass `return_ir=True` to attach the intermediate representation to the result:

```python
result = convert("report.docx", return_ir=True)
doc = result.ir  # distill.ir.Document
```

---

## Output modes

Distill supports four output formats. Set `output_format` via `ParseOptions` or
pass it as a keyword argument.

### Markdown (default)

```python
from distill import convert

result = convert("report.docx")
print(result.markdown)
```

### Chunks

Splits the document into semantic chunks suitable for embedding pipelines:

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert("report.docx", options=ParseOptions(output_format="chunks"))

for chunk in result.chunks:
    print(chunk["heading"], len(chunk["content"]))
```

### JSON

Returns a structured JSON representation of the document tree:

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert("report.docx", options=ParseOptions(output_format="json"))

import json
print(json.dumps(result.document_json, indent=2))
```

### HTML

Renders the document as semantic HTML:

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert("report.docx", options=ParseOptions(output_format="html"))
print(result.html)
```

---

## ParseOptions reference

All fields on `ParseOptions` (from `distill.parsers.base`). Pass as keyword
arguments to `convert()` or construct a `ParseOptions` instance directly.

| Field | Type | Default | Description |
|---|---|---|---|
| `image_dir` | `Optional[str]` | `None` | Directory to write extracted images to. |
| `images` | `str` | `"extract"` | Image handling mode: `"extract"`, `"suppress"`, `"inline_ocr"`, or `"caption"`. |
| `vision_provider` | `Optional[str]` | `None` | Vision model provider for image captioning: `"openai"`, `"anthropic"`, or `"ollama"`. |
| `vision_api_key` | `Optional[str]` | `None` | API key for the vision provider. |
| `max_table_rows` | `int` | `500` | Maximum rows per table. Set to `0` for unlimited. |
| `include_formulas` | `bool` | `False` | XLSX: render formula text instead of computed values. |
| `streaming` | `bool` | `False` | Enable streaming mode. |
| `min_quality` | `float` | `0.0` | Minimum quality score threshold. `0.0` disables enforcement. |
| `ocr_enabled` | `bool` | `True` | Whether OCR is available for scanned PDFs. |
| `extra` | `dict` | `{}` | Extra per-format options passed through to parsers. |
| `output_format` | `str` | `"markdown"` | Output format: `"markdown"`, `"json"`, `"html"`, or `"chunks"`. |
| `paginate_output` | `bool` | `False` | Insert page separators at page boundaries (PDF/DOCX only). |
| `extract_content` | `bool` | `False` | HTML input: strip boilerplate via trafilatura/readability-lxml. |
| `llm` | `Optional[LLMConfig]` | `None` | LLM configuration for LLM-powered features. See below. |
| `llm_merge_tables` | `bool` | `False` | Enable LLM-powered cross-page table merging (PDF only). Requires `llm`. |
| `extract` | `bool` | `False` | Enable structured JSON extraction via LLM. Requires `llm` and `schema`. |
| `schema` | `Optional[dict]` | `None` | JSON schema for structured extraction when `extract=True`. |
| `topic_segmentation` | `bool` | `False` | Enable audio topic segmentation. Requires `llm`. |
| `transcription_engine` | `str` | `"whisper"` | Audio transcription engine: `"whisper"` or `"vosk"`. |
| `whisper_model` | `str` | `"base"` | Whisper model size: `"tiny"`, `"base"`, `"small"`, `"medium"`, `"large-v3"`. |
| `hf_token` | `Optional[str]` | `None` | Hugging Face token for gated models (e.g. pyannote speaker diarization). |
| `collector` | `Optional[WarningCollector]` | `None` | Warning collector instance. Set automatically by `convert()` -- callers rarely need to set this. |

### LLMConfig

Required for `llm_merge_tables`, `extract`, and `topic_segmentation`:

```python
from distill.features.llm import LLMConfig

llm = LLMConfig(
    api_key="sk-...",
    model="gpt-4o",
    base_url=None,          # optional: custom endpoint
    timeout_seconds=30,     # default
    max_retries=2,          # default
)
```

---

## Streaming

`convert_stream()` yields Markdown one top-level section at a time, useful for
large documents where you want incremental output:

```python
from distill import convert_stream

for chunk in convert_stream("large-report.pdf"):
    print(chunk)
    print("---")
```

With metadata, the first yielded chunk is the YAML front-matter block:

```python
for i, chunk in enumerate(convert_stream("report.docx", include_metadata=True)):
    if i == 0:
        print("Front matter:", chunk)
    else:
        print("Section:", chunk[:80])
```

---

## Quality score

Every `ConversionResult` includes a quality score indicating how well structural
elements were preserved during conversion.

```python
from distill import convert

result = convert("report.docx")

# Composite score (0.0 - 1.0), or None if the quality gate fired
print(result.quality_score)

# Full per-metric breakdown
qs = result.quality_details
print(f"Headings preserved: {qs.heading_preservation:.2f}")
print(f"Tables preserved:   {qs.table_preservation:.2f}")
print(f"Lists preserved:    {qs.list_preservation:.2f}")
print(f"Token reduction:    {qs.token_reduction_ratio:.2f}")
print(f"Valid Markdown:     {qs.valid_markdown}")
print(f"Passed:             {qs.passed}")
```

Quality details fields:

| Field | Type | Description |
|---|---|---|
| `overall` | `Optional[float]` | Composite score (0.0 - 1.0). `None` when the gate fires. |
| `heading_preservation` | `float` | Ratio of headings in IR vs Markdown output. |
| `table_preservation` | `float` | Ratio of tables in IR vs GFM tables in Markdown. |
| `list_preservation` | `float` | Ratio of lists in IR vs Markdown lists. |
| `token_reduction_ratio` | `float` | `tokens(output) / tokens(naive_estimate)`. |
| `valid_markdown` | `bool` | Whether the output is parseable CommonMark. |
| `warnings` | `list[str]` | Quality-related warnings. |
| `error` | `Optional[str]` | Set when the pre-check gate fires. |

---

## Warnings

Conversion warnings appear in two forms: human-readable strings and structured
dictionaries.

```python
from distill import convert

result = convert("report.pdf")

# Human-readable warnings (list[str])
for w in result.warnings:
    print(w)

# Structured warnings (list[dict]) with type, message, pages, count
for w in result.structured_warnings:
    print(w["type"], w["message"])
    if w.get("pages"):
        print(f"  Affected pages: {w['pages']}")
```

Warning types include: `cross_page_table`, `math_detected`,
`math_conversion_partial`, `scanned_content`, and `audio_quality_low`.

---

## IR access

Use `convert_to_ir()` to get the raw intermediate representation for custom
filtering, transformation, or inspection before rendering.

```python
from distill import convert_to_ir

doc = convert_to_ir("report.docx")

# Inspect the document tree
print(f"Title: {doc.metadata.title}")
print(f"Sections: {len(doc.sections)}")

for section in doc.sections:
    print(f"  [{section.level}] {section.title}")
    for block in section.blocks:
        print(f"    {type(block).__name__}")
```

### Filter and transform

```python
from distill import convert_to_ir

doc = convert_to_ir("report.docx")

# Remove all sections deeper than level 2
doc.sections = [s for s in doc.sections if s.level <= 2]

# Render the modified tree to Markdown
markdown = doc.render()
print(markdown)
```

### Render with options

```python
doc = convert_to_ir("report.docx")

# Render with YAML front-matter
markdown = doc.render(front_matter=True)
print(markdown)
```

### Stream the IR

```python
doc = convert_to_ir("report.docx")

for chunk in doc.render_stream():
    print(chunk)
```

---

## Format-specific notes

### XLSX / XLSM

- `.xlsm` files (macro-enabled workbooks) are parsed the same as `.xlsx` --
  macros are ignored, only cell data and structure are extracted.
- Merged cells are expanded: the merged value appears in the top-left cell,
  remaining cells are empty.
- Use `include_formulas=True` to emit raw formula text (e.g. `=SUM(A1:A10)`)
  instead of computed values.
- Use `max_table_rows=0` for unlimited rows, or set a cap to truncate large
  sheets.

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert(
    "financials.xlsx",
    options=ParseOptions(max_table_rows=1000, include_formulas=True),
)
```

### PDF

**Native PDFs** (text-based) are parsed directly -- no extra dependencies needed.

**Scanned PDFs** (image-based) require OCR. Install the OCR extra and ensure
Tesseract is available on the system:

```python
# pip install "distill-core[ocr]"
from distill import convert
from distill.parsers.base import ParseOptions

result = convert("scanned.pdf", options=ParseOptions(ocr_enabled=True))
```

**Page separators**: use `paginate_output=True` to insert page boundary markers:

```python
result = convert("report.pdf", options=ParseOptions(paginate_output=True))
```

**Cross-page table merging**: when tables span page breaks, enable LLM-powered
merging:

```python
from distill.features.llm import LLMConfig

result = convert(
    "report.pdf",
    options=ParseOptions(
        llm=LLMConfig(api_key="sk-...", model="gpt-4o"),
        llm_merge_tables=True,
    ),
)
```

### Audio

Audio conversion requires the `[audio]` extra:

```bash
pip install "distill-core[audio]"
```

Audio files must be submitted via the async API (Celery worker). Direct
`convert()` calls for audio files raise a `ParseError` with instructions.

**Whisper model sizes** (speed vs accuracy trade-off):

| Model | Parameters | Relative speed | English accuracy |
|---|---|---|---|
| `tiny` | 39 M | fastest | lower |
| `base` | 74 M | fast | good |
| `small` | 244 M | moderate | better |
| `medium` | 769 M | slow | high |
| `large-v3` | 1550 M | slowest | highest |

**Speaker diarization** (who said what) requires a Hugging Face token with access
to gated pyannote models:

```python
from distill.parsers.base import ParseOptions

options = ParseOptions(
    transcription_engine="whisper",
    whisper_model="small",
    hf_token="hf_...",
)
```

**Topic segmentation** groups transcript sections by topic. Requires an LLM:

```python
from distill.parsers.base import ParseOptions
from distill.features.llm import LLMConfig

options = ParseOptions(
    topic_segmentation=True,
    llm=LLMConfig(api_key="sk-...", model="gpt-4o"),
)
```

### Google Workspace

Google Docs, Sheets, and Slides are fetched via the Google Drive export API.
Pass the Drive file URL as `source` and an OAuth access token in
`options.extra`:

```python
from distill import convert
from distill.parsers.base import ParseOptions

options = ParseOptions(extra={"access_token": "ya29.a0..."})
result = convert(
    "https://docs.google.com/document/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ/edit",
    options=options,
)
print(result.markdown)
```

Requires the `[google]` extra:

```bash
pip install "distill-core[google]"
```

### HTML

For web pages with boilerplate (navbars, ads, footers), enable content
extraction to isolate the main article body:

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert("page.html", options=ParseOptions(extract_content=True))
```

Requires the `[html]` extra:

```bash
pip install "distill-core[html]"
```

---

## Structured JSON extraction

Extract structured data from any document using an LLM and a JSON schema:

```python
from distill import convert
from distill.parsers.base import ParseOptions
from distill.features.llm import LLMConfig

schema = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": "string"},
        "total_amount": {"type": "number"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                },
            },
        },
    },
}

result = convert(
    "invoice.pdf",
    options=ParseOptions(
        extract=True,
        schema=schema,
        llm=LLMConfig(api_key="sk-...", model="gpt-4o"),
    ),
)

print(result.extracted)
# {"invoice_number": "INV-2024-001", "total_amount": 1500.00, "line_items": [...]}
```

---

## System dependencies

Some formats require external programs installed on the host system.

| Dependency | Required for | Install |
|---|---|---|
| **LibreOffice** | `.doc`, `.xls`, `.ppt`, `.odt`, `.ods`, `.odp` (legacy formats) | `apt install libreoffice-core` (Debian/Ubuntu) or [libreoffice.org](https://www.libreoffice.org/) |
| **Tesseract** | Scanned PDF OCR (`distill-core[ocr]`) | `apt install tesseract-ocr` (Debian/Ubuntu) or [github.com/tesseract-ocr](https://github.com/tesseract-ocr/tesseract) |

Python-only formats (DOCX, XLSX, PPTX, native PDF, HTML, JSON, WSDL, EPUB,
SQL) have no system dependencies beyond the Python packages installed by pip.
