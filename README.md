# ⚗️ Distill

**Convert any document format to clean, LLM-optimized Markdown.**

[![CI](https://github.com/lakshgk/distill/actions/workflows/ci.yml/badge.svg)](https://github.com/lakshgk/distill/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)

Distill extracts semantic structure from Word, Excel, PowerPoint, PDF, and Google Workspace files and renders it as clean, token-efficient Markdown — purpose-built for LLM pipelines, RAG systems, and document search.

---

## Two ways to use Distill

| | Library | Self-hosted service |
|---|---|---|
| Install | `pip install distill-core` | `docker compose up -d` |
| Usage | Call `convert()` in Python | `POST /api/convert` REST endpoint |
| Infrastructure | None | Docker, Redis, Celery |
| Best for | Embedding in your own app | Teams sharing a conversion service |

-> [Library quickstart](docs/quickstart-library.md)
-> [Service quickstart](docs/quickstart-service.md)

---

## Features

- **19 formats supported** — DOCX, DOC, ODT, XLSX, XLS, XLSM, CSV, PPTX, PPT, PDF (native + scanned), HTML, EPUB, WSDL, JSON, SQL, Audio (MP3/WAV/M4A/FLAC/OGG), Google Docs, Sheets, Slides
- **Structure preserved** — headings, tables, lists, bold/italic, code blocks, hyperlinks, speaker notes
- **Token-efficient output** — 50–70% fewer tokens than naive extraction
- **Quality score** — every conversion reports how much structure was preserved (0–1 scale)
- **Multiple output formats** — Markdown, RAG chunks, JSON IR export, semantic HTML
- **Structured warnings** — typed, machine-readable warnings for math, scanned content, truncated tables, and more
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

## Configuration

Copy `.env.example` to `.env` and fill in the values for the features you need:

```bash
cp .env.example .env
```

| Variable | Purpose |
|----------|---------|
| `REDIS_URL` | Redis broker for async jobs |
| `DISTILL_LLM_API_KEY` | Enables LLM-powered features (table merging, extraction, audio topic segmentation) |
| `DISTILL_LLM_MODEL` | Model to use (e.g. `gpt-4o`, `claude-3-5-sonnet`) |
| `DISTILL_VISION_API_KEY` | Enables image captioning |
| `DISTILL_VISION_PROVIDER` | Vision backend: `openai`, `anthropic`, or `ollama` |
| `DISTILL_HF_TOKEN` | Enables speaker diarization in audio pipeline |
| `DISTILL_GOOGLE_CREDENTIALS_PATH` | Service account for Google Workspace input |

See [docs/architecture.md](docs/architecture.md) (Configuration Reference) for
the complete variable list with defaults and feature availability matrix.

Credentials set as environment variables are never logged. The system redacts
sensitive fields before any log output.

`DISTILL_HF_TOKEN` is required for speaker labels in audio transcription.
See [docs/architecture.md](docs/architecture.md) (Audio Pipeline → Setup steps)
for instructions on obtaining a Hugging Face token and accepting the pyannote
model licence.

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
| Microsoft Word | `.docx`, `.doc`, `.odt` | `.doc` and `.odt` require LibreOffice |
| Microsoft Excel | `.xlsx`, `.xlsm`, `.xls`, `.csv` | `.xls` requires LibreOffice; `.xlsm` macros stripped |
| Microsoft PowerPoint | `.pptx`, `.ppt` | `.ppt` requires LibreOffice |
| PDF (native) | `.pdf` | Text layer extracted via pdfplumber |
| PDF (scanned) | `.pdf` | Image-only PDFs — requires `distill-core[ocr]` |
| HTML | `.html`, `.htm` | Boilerplate removal via `distill-core[html]` (opt-in) |
| Google Docs | Drive URL | Requires `distill-core[google]` |
| Google Sheets | Drive URL | Requires `distill-core[google]` |
| Google Slides | Drive URL | Requires `distill-core[google]` |
| Audio | `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg` | Optional — `pip install distill-core[audio]`. Async only (requires Redis + Celery worker) |
| EPUB | `.epub` | Requires `pip install "distill-core[epub]"` |
| WSDL | `.wsdl`, `.wsd` | SOAP web service descriptions (WSDL 1.1 and 2.0) |
| JSON | `.json` | JSON Schema, API dumps, flat objects — auto-detected |
| SQL | `.sql` | DDL rendered as structured Markdown. Requires `pip install "distill-core[sql]"` |

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
# Default: Markdown output
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

The `output_format` field controls the response envelope:

| `output_format` | Response keys |
|-----------------|---------------|
| `markdown` (default) | `markdown`, `quality`, `stats`, `warnings` |
| `chunks` | `chunks`, `chunk_count`, `quality`, `stats`, `warnings` |
| `json` | `document`, `quality`, `stats`, `warnings` |
| `html` | `html`, `quality`, `stats`, `warnings` |

```bash
# RAG chunks
curl -X POST http://localhost:7860/api/convert \
  -F "file=@report.pdf" \
  -F "output_format=chunks"
```

### Priority queues

For async jobs, an optional `priority` field controls queue routing:

| `priority` | Queue | Meaning |
|------------|-------|---------|
| `interactive` | `distill.interactive` | Human is waiting — never starved |
| `batch` | `distill.batch` | Background processing — can wait |
| _(omitted)_ | Auto-routed | Based on file size and format |

```bash
# Force a job to the batch queue
curl -X POST http://localhost:7860/api/convert \
  -F "file=@large_report.pdf" \
  -F "priority=batch"
```

When `priority` is omitted, routing is automatic: audio files always go to
batch, files above the size threshold go to batch, and everything else goes
to interactive.

### Streaming job progress

For async jobs, connect to the SSE endpoint to receive real-time progress events:

```bash
# Stream progress events (curl)
curl -N -H "Accept: text/event-stream" \
  http://localhost:7860/jobs/{job_id}/stream
```

```javascript
// Stream progress events (browser)
const source = new EventSource(`/jobs/${jobId}/stream`);
source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`${data.stage}: ${data.pct}%`);
  if (data.status === 'completed' || data.status === 'failed') {
    source.close();
  }
};
```

Each event is a JSON object with `job_id`, `status`, `stage`, `pct` (0-100),
`queue`, `message`, and `ts` fields. The stream closes automatically when the
job completes or fails. A heartbeat comment is sent every 15 seconds to keep
proxy connections alive.

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTILL_SSE_KEEPALIVE_SECONDS` | `15` | Heartbeat interval for idle connections |
| `DISTILL_SSE_MAX_DURATION_SECONDS` | `3600` | Max stream duration before auto-close |

### Webhook callbacks

For async jobs (large files, audio), supply a `callback_url` to receive the
result via POST when conversion completes — no polling required:

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@large_report.pdf" \
  -F "callback_url=https://your-server.com/distill-webhook"
```

- **HTTPS only** — `http://` is rejected with HTTP 422
- **No private IPs** — localhost and private ranges are rejected (SSRF protection)
- **Retry policy** — 3 attempts with 1s/2s/4s backoff on delivery failure
- **Sync jobs** — `callback_url` is silently ignored for small files that
  complete synchronously (result returned directly in the response)
- The callback payload matches the `GET /jobs/{id}` response exactly

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

## Output formats

By default `convert()` returns Markdown. Pass `output_format` to `ParseOptions` to switch:

```python
from distill import convert
from distill.parsers.base import ParseOptions

# RAG-ready chunks — each chunk is a semantic unit with heading path and token count
result = convert("report.pdf", options=ParseOptions(output_format="chunks"))
for chunk in result.chunks:
    print(chunk.chunk_id)       # stable ID derived from document + position
    print(chunk.heading_path)   # e.g. "Executive Summary > Revenue"
    print(chunk.content)        # Markdown text of this chunk
    print(chunk.token_count)    # estimated token count (len(content) // 4)

# Full IR as a JSON dict — useful for custom post-processing
result = convert("report.docx", options=ParseOptions(output_format="json"))
print(result.document_json)    # {"title": ..., "format": ..., "nodes": [...]}

# Semantic HTML — headings, tables, lists, no inline styles
result = convert("report.docx", options=ParseOptions(output_format="html"))
print(result.html)             # "<h1>...</h1><table>...</table>..."
```

---

## HTML input

Convert `.html` and `.htm` files to any output format:

```python
from distill import convert
from distill.parsers.base import ParseOptions

# Basic — parse structure as-is
result = convert("page.html")
print(result.markdown)

# With boilerplate removal (requires pip install "distill-core[html]")
result = convert(
    "page.html",
    options=ParseOptions(extra={"extract_content": True}),
)
print(result.markdown)   # navigation, footers, and ads stripped
```

`extract_content` uses trafilatura first, then readability-lxml as fallback. If neither is installed the raw HTML is parsed without stripping. HTML input supports all output formats (`markdown`, `chunks`, `json`, `html`).

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

## Async jobs and webhooks

Large files (above `DISTILL_ASYNC_SIZE_THRESHOLD_MB`, default 10 MB) and all audio files are processed asynchronously via Celery workers. The API returns HTTP 202 with a `job_id` that you can poll:

```bash
# Poll job status
curl http://localhost:7860/jobs/{job_id}
```

Or supply a `callback_url` to receive the result via webhook when the job completes:

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@large_report.pdf" \
  -F "callback_url=https://your-server.com/distill-webhook"
```

The `priority` field controls queue routing: `interactive` (human-waiting) or `batch` (background). When omitted, routing is automatic based on file size and format.

See [API reference](docs/api-reference.md) for full parameter documentation.

---

## Real-time progress

For async jobs, connect to the SSE endpoint to receive live progress events:

```bash
curl -N http://localhost:7860/jobs/{job_id}/stream
```

```javascript
const source = new EventSource(`/jobs/${jobId}/stream`);
source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`${data.stage}: ${data.pct}%`);
  if (data.status === 'completed' || data.status === 'failed') {
    source.close();
  }
};
```

See [API reference](docs/api-reference.md) for the full event schema.

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

## Running the Worker

Distill supports async processing for large files via Celery + Redis. Small files
are still processed synchronously for lowest latency.

### Local development (single machine)

Start Redis, the API server, and workers in separate terminals:

```bash
# Terminal 1 — Redis
docker run -d --name distill-redis -p 6379:6379 redis:7-alpine

# Terminal 2 — API server
cd packages/app
py -m distill_app

# Terminal 3 — Interactive worker (Linux/macOS)
cd packages/app
celery -A distill_app.worker worker --loglevel=info --concurrency=4 \
  --queues=distill.interactive --hostname=worker-interactive@%h

# Terminal 4 — Batch worker (Linux/macOS)
cd packages/app
celery -A distill_app.worker worker --loglevel=info --concurrency=2 \
  --queues=distill.batch --hostname=worker-batch@%h

# Windows — use solo pool for both workers
celery -A distill_app.worker worker --loglevel=info --pool=solo \
  --queues=distill.interactive --hostname=worker-interactive@%h

celery -A distill_app.worker worker --loglevel=info --pool=solo \
  --queues=distill.batch --hostname=worker-batch@%h
```

> **Note:** The Celery worker is only required for audio files and large documents
> (>10 MB). All other formats are converted synchronously by the API server alone.
> On Windows, the `--pool=solo` flag is required because the default `prefork`
> pool has permission errors with `billiard` on Windows.

### Docker Compose (production)

Bring up all four services with a single command:

```bash
cp .env.example .env   # adjust settings as needed
docker compose up -d
```

This starts:
- **redis** — Redis 7 with health check
- **api** — FastAPI server on port 7860
- **worker-interactive** — Celery worker consuming the `distill.interactive` queue
- **worker-batch** — Celery worker consuming the `distill.batch` queue
- **flower** — Celery Flower monitoring dashboard at http://localhost:5555

Scale workers independently to match your workload:

```bash
docker compose up -d --scale worker-interactive=4 --scale worker-batch=2
```

Worker concurrency is configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DISTILL_INTERACTIVE_CONCURRENCY` | `4` | Processes per interactive worker container |
| `DISTILL_BATCH_CONCURRENCY` | `2` | Processes per batch worker container |

### Monitoring

[Celery Flower](https://flower.readthedocs.io/) can be used to monitor task
queues and worker health. It is not included in the default stack but can be
added as a service in `docker-compose.yml`:

```bash
pip install flower
celery -A distill_app.worker flower --port=5555
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `10` | Files above this size (MB) go async |
| `DISTILL_JOB_TTL_SECONDS` | `3600` | How long job results stay in Redis |
| `DISTILL_WORKER_CONCURRENCY` | `4` | Celery worker process count |

See `.env.example` for the full list with comments.

---

## Repository metrics

Clone and view traffic is exported nightly to [`data/traffic/`](data/traffic/).
See [`data/traffic/summary.md`](data/traffic/summary.md) for a 30-day snapshot.

---

## Documentation

| Guide | Description |
|---|---|
| [Library quickstart](docs/quickstart-library.md) | Using distill-core as a Python library |
| [Service quickstart](docs/quickstart-service.md) | Self-hosting with Docker Compose |
| [API reference](docs/api-reference.md) | Full REST API documentation |
| [Configuration](docs/configuration.md) | All environment variables |
| [Architecture](docs/architecture.md) | Internal design and IR |
| [Parsers](docs/parsers.md) | Per-format parser reference |

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](docs/CONTRIBUTING.md).

---

## License

MIT © [lakshgk](https://github.com/lakshgk)
