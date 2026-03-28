# Distill — Architecture

## Overview

Distill converts documents (DOCX, PDF, XLSX, PPTX, Google Workspace) into
clean, LLM-optimised Markdown. The pipeline has three stages: **parse → IR →
render**.

```
  source file
      │
      ▼
  ┌─────────┐   ParseOptions
  │  Parser │◄──────────────
  └────┬────┘
       │  Document (IR)
       ▼
  ┌──────────┐   render(front_matter=…)
  │ Renderer │─────────────────────────►  Markdown string
  └──────────┘
       │
       ▼
  ┌─────────┐
  │ Quality │──► QualityScore
  └─────────┘
```

The registry wires format detection to the correct parser; callers use
`convert()` or `convert_to_ir()` and never touch parsers directly.

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
  app/             # distill-app: Gradio desktop UI
    distill_app/
      ui.py        # Gradio Blocks layout + convert_file(), quality_badge()
    tests/
      test_ui.py
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

### DocumentMetadata fields (Phase 1)

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
| `extra` | `{}` | Per-parser overrides (e.g. `max_file_size`, `max_unzip_size`) |

### Routing and quality gating (Phase 1)

| Format | Primary | Fallback |
|--------|---------|----------|
| `.docx` | mammoth → HTML → IR | pandoc → GFM (if mammoth yields no content) |
| `.doc` | LibreOffice → .docx → DocxParser | — |
| `.pdf` native | pdfplumber (text + tables) | — |
| `.pdf` scanned | docling or Tesseract | (Phase 4) |
| `.xlsx` | openpyxl | — |
| `.xls` | LibreOffice → .xlsx → XlsxParser | — |
| `.pptx` | python-pptx | — |
| `.ppt` | LibreOffice → .pptx → PptxParser | — |
| `.gdoc` / Drive URL | Drive API export → .docx → DocxParser | — |
| `.gsheet` / Drive URL | Drive API export → .xlsx → XlsxParser | — |
| `.gslides` / Drive URL | Drive API export → .pptx → PptxParser | — |

---

## Renderer

`MarkdownRenderer` converts an IR `Document` to CommonMark Markdown.

Key behaviours:

- `front_matter=False` (default): YAML metadata block is suppressed.
- `front_matter=True`: emits a YAML front-matter block containing all non-empty
  `DocumentMetadata` fields at the top of the output.  Opt-in behaviour — pass
  `include_metadata=True` to `convert()` or `front_matter=True` to
  `MarkdownRenderer` to enable.
- Images: renders the richest available representation in priority order:
  `structured_data > ocr_text > caption > alt_text > (suppress if decorative)`.
- Tables: GFM pipe tables.
- Lists: CommonMark `-` bullets or `1.` numbered lists; nested lists indented.
- Code blocks: fenced with optional language hint.

---

## Quality Scoring

`distill.quality.score(ir, markdown)` returns a `QualityScore` with:

| Metric | Weight | Measures |
|--------|--------|---------|
| `heading_preservation` | 25% | Headings in IR vs headings in Markdown output |
| `table_preservation` | 25% | Tables in IR vs GFM table separators in output |
| `list_preservation` | 15% | List items in IR vs Markdown list items |
| `token_reduction_ratio` | 20% | Token efficiency of Markdown vs naive estimate |
| `valid_markdown` | 15% | Output passes CommonMark validation (assumed true, Phase 5 adds linter) |

`QualityScore.passed` returns `True` if `overall >= 0.70`.

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

# One-call conversion
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
print(result.quality_score)  # 0.0 – 1.0
print(result.metadata.title)
print(result.warnings)

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

---

## Phase Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | DOCX + PDF parsers, IR, renderer, quality, security baseline | Complete |
| 2 | XLSX + PPTX parsers, legacy .doc/.xls/.ppt via LibreOffice | Complete |
| 3 | Google Workspace parsers (Docs/Sheets/Slides via Drive API) | Complete |
| 4 | Scanned PDF OCR (docling + Tesseract) | Complete |
| 5a | Streaming API (`convert_stream`) | Complete |
| 5b-i | Gradio UI (`distill-app`) | Complete |
| 5c | Vision captioning (`distill-core[vision]`) | Complete |
| 5e | PyPI publishing (CI workflows) | Complete |
| 5b-ii, 5d | Docker, Hardening/CI corpus | Pending |
