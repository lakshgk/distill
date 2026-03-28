# ⚗️ Distill

**Convert any document format to clean, LLM-optimized Markdown.**

[![CI](https://github.com/lakshgk/distill/actions/workflows/ci.yml/badge.svg)](https://github.com/lakshgk/distill/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![PyPI version](https://img.shields.io/pypi/v/distill-core.svg)](https://pypi.org/project/distill-core/)
[![PyPI downloads](https://img.shields.io/pypi/dm/distill-core.svg)](https://pypi.org/project/distill-core/)

Distill extracts semantic structure from Word, Excel, PowerPoint, PDF, and Google Workspace files and renders it as clean, token-efficient Markdown — purpose-built for LLM pipelines, RAG systems, and document search.

---

## Who is this for?

- **LLM and RAG pipeline builders** — feed structured Markdown instead of raw binary files into your vector store or language model. Every heading, table, and list survives the conversion.
- **Document automation teams** — batch-process archives of Word, Excel, and PowerPoint files into a consistent, machine-readable format without writing custom parsers for each format.
- **Developers building document search** — structured output means your search index reflects the actual shape of the document, not a flat wall of text.
- **Anyone replacing naive extraction** — stop losing 50–70% of your token budget to formatting noise and structural loss.

## Features

- **11 formats supported** — DOCX, DOC, XLSX, XLS, CSV, PPTX, PPT, PDF (native + scanned), Google Docs, Sheets, Slides
- **Structure preserved** — headings, tables, lists, bold/italic, code blocks, hyperlinks, speaker notes
- **Token-efficient output** — 50–70% fewer tokens than naive extraction
- **Quality score** — every conversion reports how much structure was preserved (0–1 scale)
- **Scanned PDF OCR** — auto-detect image-only PDFs and run layout-aware OCR via docling or Tesseract
- **Vision captioning** — describe images in documents using OpenAI, Anthropic, or Ollama
- **Streaming API** — yield one Markdown chunk per section for real-time LLM pipelines
- **IR access** — get the parsed document tree to filter, transform, or render to any format
- **Web UI + REST API** — local browser UI and `POST /api/convert` endpoint, no cloud required
- **Security baseline** — input size limits, zip bomb detection, XXE prevention, encrypted PDF detection

---

## Why Distill?

Feeding raw office documents into an LLM wastes tokens and loses structure. Distill solves both:

| Format | Naive extraction | Distill output | Token reduction |
|--------|-----------------|----------------|-----------------|
| DOCX   | Raw OOXML / flat text | Structured Markdown with headings + tables | ~60% |
| XLSX   | Cell-by-cell dump | GFM pipe tables, one section per sheet | ~70% |
| PPTX   | Slide text fragments | Headed sections + tables + speaker notes | ~55% |
| PDF    | Character stream | Structured text + extracted tables | ~50% |

---

## Install

```bash
# Core library — DOCX, XLSX, PPTX, CSV, native PDF
pip install distill-core

# + Scanned PDF support (OCR via docling or Tesseract)
pip install "distill-core[ocr]"

# + Google Workspace (Docs, Sheets, Slides via Drive API)
pip install "distill-core[google]"

# + Vision captioning for images (OpenAI / Anthropic / Ollama)
pip install "distill-core[vision]"

# + Web UI and REST API
pip install distill-app
```

---

## Quick start

```python
from distill import convert

result = convert("report.docx")

print(result.markdown)        # Clean Markdown string
print(result.quality_score)   # 0.0 – 1.0 (how much structure was preserved)
print(result.metadata.title)  # Document title
print(result.warnings)        # Any conversion warnings
```

---

## Supported formats

| Format | Extensions | Notes |
|--------|-----------|-------|
| Microsoft Word | `.docx`, `.doc` | `.doc` requires LibreOffice |
| Microsoft Excel | `.xlsx`, `.xls`, `.csv` | `.xls` requires LibreOffice |
| Microsoft PowerPoint | `.pptx`, `.ppt` | `.ppt` requires LibreOffice |
| PDF (native) | `.pdf` | Text layer extracted via pdfplumber |
| PDF (scanned) | `.pdf` | Image-only PDFs — requires `distill-core[ocr]` |
| Google Docs | Drive URL | Requires `distill-core[google]` |
| Google Sheets | Drive URL | Requires `distill-core[google]` |
| Google Slides | Drive URL | Requires `distill-core[google]` |

---

## Web UI

```bash
pip install distill-app
distill-app          # opens http://localhost:7860
```

Upload any supported file, preview the Markdown output, and download the result. No cloud, no account required.

### REST API

The same server also exposes a REST endpoint:

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@report.pdf" \
  -F "include_metadata=true" \
  -F "max_rows=500" \
  -F "enable_ocr=false"
```

```json
{
  "markdown": "# Report\n\n...",
  "quality":  { "overall": 0.92, "headings": 1.0, "tables": 0.85, "lists": 1.0, "efficiency": 0.78 },
  "stats":    { "words": 1420, "pages": 5, "format": "PDF" },
  "warnings": []
}
```

---

## Quality score

Every conversion returns a `quality_score` between 0 and 1 that measures how much of the source document's structure made it into the Markdown output.

```python
result = convert("report.docx")

print(result.quality_score)           # 0.92 — overall
print(result.quality_details.heading_preservation)   # 1.0  — all headings present
print(result.quality_details.table_preservation)     # 0.85 — most tables preserved
print(result.quality_details.list_preservation)      # 1.0  — all lists present
print(result.quality_details.token_reduction_ratio)  # 0.78 — 22% token savings
```

A score above 0.70 is considered a passing conversion. Below that, check `result.warnings` for what went wrong.

---

## Streaming output

Yield one Markdown chunk per document section — useful for streaming directly into an LLM without buffering the whole document.

```python
from distill import convert_stream

for chunk in convert_stream("report.docx"):
    print(chunk)

# Include YAML front-matter as the first chunk
for chunk in convert_stream("report.docx", include_metadata=True):
    print(chunk)
```

---

## Scanned PDF (OCR)

Distill automatically detects image-only PDFs. When `enable_ocr=True`, it runs layout-aware OCR to extract headings, paragraphs, tables, and lists from the page image.

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert(
    "scanned_contract.pdf",
    options=ParseOptions(extra={"enable_ocr": True}),
)
```

Two OCR backends are supported, tried in order:
- **docling** — layout-aware, understands tables and headings
- **Tesseract** — lightweight fallback

Install both with `pip install "distill-core[ocr]"`.

---

## Google Workspace

```python
result = convert(
    "https://docs.google.com/document/d/FILE_ID/edit",
    options=ParseOptions(extra={"access_token": "ya29..."})
)
```

Google Docs, Sheets, and Slides are exported via the Drive API and processed through the same parser pipeline as their Office equivalents.

---

## Vision captioning

Add AI-generated descriptions for images embedded in documents:

```python
from distill import convert
from distill.parsers.base import ParseOptions

result = convert(
    "report.docx",
    options=ParseOptions(
        images="caption",
        vision_provider="openai",   # or "anthropic" / "ollama"
        vision_api_key="sk-...",
    ),
)
```

Requires `pip install "distill-core[vision]"`.

---

## IR access — parse once, render anywhere

Every conversion goes through an **Intermediate Representation (IR)** — a structured document tree that sits between the raw file and the Markdown output:

```
source file  →  parser  →  IR Document  →  renderer  →  Markdown
```

The IR is exposed via `convert_to_ir()`. Use it when you want to post-process structure before rendering, or render to a format other than Markdown:

```python
from distill import convert_to_ir

doc = convert_to_ir("report.pdf")

# Inspect structure
for section in doc.sections:
    print(section.heading, "—", len(section.blocks), "blocks")

# Filter — keep only sections with headings
doc.sections = [s for s in doc.sections if s.heading]

# Extract all tables
from distill.ir import Table
tables = [
    block
    for section in doc.sections
    for block in section.blocks
    if isinstance(block, Table)
]

# Render to Markdown
markdown = doc.render()

# Or stream
for chunk in doc.render_stream():
    print(chunk)
```

---

## Metadata

Set `include_metadata=True` to include document properties as a YAML front-matter block:

```python
result = convert("report.docx", include_metadata=True)
# result.markdown starts with:
# ---
# title: Quarterly Report
# author: Jane Smith
# created_at: 2024-01-15T10:30:00
# word_count: 3420
# page_count: 12
# ---
```

---

## Project structure

```
distill/
├── packages/
│   ├── core/          # distill-core: the conversion library
│   │   ├── distill/
│   │   │   ├── ir.py          # Intermediate Representation
│   │   │   ├── registry.py    # Parser registry
│   │   │   ├── renderer.py    # IR → Markdown
│   │   │   ├── quality.py     # Quality scoring
│   │   │   └── parsers/       # Format-specific parsers
│   │   └── tests/
│   └── app/           # distill-app: web UI + REST API
└── docs/              # Architecture, parser reference, contributing
```

---

## Local development

```bash
git clone https://github.com/lakshgk/distill.git
cd distill

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e "packages/core[dev,google,vision,ocr]"
pip install -e packages/app

pytest packages/core/tests -v
pytest packages/app/tests  -v

distill-app
```

> `.doc`, `.xls`, and `.ppt` require [LibreOffice](https://www.libreoffice.org/download/download-libreoffice/) on your `PATH`.
> Scanned PDF OCR requires Tesseract: `brew install tesseract` / `apt install tesseract-ocr`.

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## License

MIT © [lakshgk](https://github.com/lakshgk)
