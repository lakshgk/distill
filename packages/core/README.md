# ⚗️ Distill

**Convert any document format to clean, LLM-optimized Markdown.**

[![CI](https://github.com/lakshgk/distill/actions/workflows/ci.yml/badge.svg)](https://github.com/lakshgk/distill/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)

Distill extracts semantic structure from Word, Excel, PowerPoint, PDF, and Google Workspace files and renders it as clean, token-efficient Markdown — purpose-built for LLM pipelines, RAG systems, and document search.

---

## Why Distill?

Feeding raw office documents into an LLM wastes tokens and loses structure. Distill solves both:

| Format | Naive extraction | Distill output | Token reduction |
|--------|-----------------|----------------|-----------------|
| DOCX   | Raw OOXML / flat text | Structured Markdown | ~60% |
| XLSX   | Cell-by-cell dump | GFM pipe tables | ~70% |
| PPTX   | Slide text fragments | Headed sections + tables | ~55% |
| PDF    | Character stream | Structured text + tables | ~50% |

---

## Install

```bash
# Core library (DOCX, XLSX, PPTX, native PDF)
pip install distill-core

# + Scanned PDF support (OCR)
pip install "distill-core[ocr]"

# + Google Workspace (Docs, Sheets, Slides)
pip install "distill-core[google]"

# + Vision captioning (image alt-text via OpenAI / Anthropic / Ollama)
pip install "distill-core[vision]"

# + Desktop UI
pip install distill-app
```

---

## Quick start

```python
from distill import convert

# Convert any supported file
result = convert("report.docx")

print(result.markdown)        # Markdown string
print(result.quality_score)   # 0.0 – 1.0
print(result.metadata.title)  # Document title
print(result.warnings)        # Any conversion warnings
```

### Desktop UI

```bash
distill-app          # opens browser at http://localhost:7860
```

### Streaming output

```python
from distill import convert_stream

# Yields one Markdown chunk per section — ideal for LLM pipelines
for chunk in convert_stream("report.docx"):
    print(chunk)

# With YAML front-matter as the first chunk
for chunk in convert_stream("report.docx", include_metadata=True):
    print(chunk)
```

### Power users: IR access

```python
from distill import convert_to_ir

ir = convert_to_ir("report.pdf")

# Filter sections, transform tables, strip images...
ir.sections = [s for s in ir.sections if s.heading]

# Then render
markdown = ir.render()

# Or stream
for chunk in ir.render_stream():
    print(chunk)
```

### Google Workspace

```python
result = convert(
    "https://docs.google.com/document/d/FILE_ID/edit",
    extra={"access_token": "ya29..."}
)
```

---

## Supported formats

| Format | Extensions | Notes |
|--------|-----------|-------|
| Microsoft Word | `.docx`, `.doc` | `.doc` requires LibreOffice |
| Microsoft Excel | `.xlsx`, `.xls`, `.csv` | `.xls` requires LibreOffice |
| Microsoft PowerPoint | `.pptx`, `.ppt` | `.ppt` requires LibreOffice |
| PDF (native) | `.pdf` | pdfplumber |
| PDF (scanned) | `.pdf` | Requires `distill-core[ocr]` |
| Google Docs | Drive URL | Requires `distill-core[google]` |
| Google Sheets | Drive URL | Requires `distill-core[google]` |
| Google Slides | Drive URL | Requires `distill-core[google]` |

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
│   └── app/           # distill-app: Gradio UI
└── docs/
```

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](docs/CONTRIBUTING.md).

To add a new format parser, implement the `Parser` base class and register it:

```python
from distill.parsers.base import Parser
from distill.registry import registry

@registry.register
class MyFormatParser(Parser):
    extensions = [".myext"]
    mime_types = ["application/x-myformat"]
    requires   = ["my-library"]

    def parse(self, source, options=None):
        # return a distill.ir.Document
        ...
```

---

## License

MIT © [lakshgk](https://github.com/lakshgk)
