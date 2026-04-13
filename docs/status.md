# Distill — Feature Status

This document tracks every feature that has been built, tested, and confirmed.
It is updated after each feature is verified by the developer.

Last updated: 2026-04-13

---

## Core Library (distill-core)

### Parsers

| Parser | Extensions | Status | Notes |
|--------|-----------|--------|-------|
| DocxParser | `.docx` | Shipped | mammoth + python-docx |
| DocLegacyParser | `.doc` | Shipped | Requires LibreOffice |
| OdtParser | `.odt` | Shipped | Requires LibreOffice |
| PdfParser | `.pdf` (native) | Shipped | pdfplumber |
| PdfParser (OCR) | `.pdf` (scanned) | Shipped | docling / Tesseract, requires `[ocr]` |
| XlsxParser | `.xlsx`, `.xlsm`, `.csv` | Shipped | openpyxl; `.xlsm` macros stripped |
| XlsLegacyParser | `.xls` | Shipped | Requires LibreOffice |
| PptxParser | `.pptx` | Shipped | python-pptx |
| PptLegacyParser | `.ppt` | Shipped | Requires LibreOffice |
| HTMLParser | `.html`, `.htm` | Shipped | Boilerplate removal via `[html]` |
| AudioParser | `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg` | Shipped | Whisper transcription, requires `[audio]` |
| EPUBParser | `.epub` | Shipped | ZIP + OPF + HTMLParser delegation, requires `[epub]` |
| WSDLParser | `.wsdl`, `.wsd` | Shipped | WSDL 1.1/2.0 via defusedxml |
| JSONParser | `.json` | Shipped | Auto-detect schema/dump/flat/code |
| SQLParser | `.sql` | Shipped | DDL structured rendering via sqlparse, requires `[sql]` |
| GoogleDocsParser | Drive URL | Shipped | Requires `[google]` |
| GoogleSheetsParser | Drive URL | Shipped | Requires `[google]` |
| GoogleSlidesParser | Drive URL | Shipped | Requires `[google]` |

### Output Formats

| Format | Status | Notes |
|--------|--------|-------|
| Markdown (default) | Shipped | CommonMark via MarkdownRenderer |
| RAG chunks | Shipped | ChunksRenderer with heading paths and token counts |
| JSON IR export | Shipped | JSONRenderer — full document tree as dict |
| Semantic HTML | Shipped | HTMLRenderer — headings, tables, lists, no inline styles |

### LLM Features

| Feature | Status | Notes |
|---------|--------|-------|
| Cross-page table merging | Shipped | PDF only, requires LLM config |
| Structured JSON extraction | Shipped | Schema-driven extraction via LLM |
| Math detection | Shipped | PDF (font/Unicode with density threshold) and DOCX (OMML) |
| Audio topic segmentation | Shipped | Batched LLM calls for topic headings |

### Quality & Warnings

| Feature | Status | Notes |
|---------|--------|-------|
| Quality scoring | Shipped | Weighted metrics: headings, tables, lists, efficiency |
| Structured warnings | Shipped | Typed, machine-readable WarningCollector |
| HF warning suppression | Shipped | Filters stderr during OCR/diarization |

### Other Core Features

| Feature | Status | Notes |
|---------|--------|-------|
| Streaming output | Shipped | `convert_stream()` — one chunk per section |
| IR access | Shipped | `convert_to_ir()` — filter, transform, render |
| Vision captioning | Shipped | OpenAI / Anthropic / Ollama backends, requires `[vision]` |
| Metadata extraction | Shipped | YAML front-matter via `include_metadata=True` |
| Merged cell expansion | Shipped | XLSX merged cells fully populated |
| PDF quality backlog | Shipped | Rotated text, table filters, heading detection, cross-page tables, footer suppression, font encoding warning |
| PPTX quality backlog | Shipped | Bullet lists, heuristic titles, alt text, footer placeholders |
| DOCX H1 demotion fix | Shipped | Explicit mammoth style map for Heading 1 |
| XLSX quality backlog | Shipped | Formula annotation, ghost rows, datetime headers |

---

## App Layer (distill-app)

### API & Server

| Feature | Status | Notes |
|---------|--------|-------|
| `POST /api/convert` | Shipped | File upload, all form fields, sync + async |
| `GET /jobs/{id}` | Shipped | Job polling with status, result, error |
| `GET /jobs/{id}/stream` | Shipped | SSE progress streaming with heartbeat |
| Web UI | Shipped | Browser interface at `GET /` |

### Async Infrastructure

| Feature | Status | Notes |
|---------|--------|-------|
| Celery + Redis job queue | Shipped | Auto-async for large files and audio |
| Priority queues | Shipped | `distill.interactive` + `distill.batch` |
| Queue routing | Shipped | Auto by size/format, manual via `priority` field |
| Job store (Redis) | Shipped | TTL-based, full lifecycle tracking |
| Progress publishing | Shipped | Redis pub/sub, ProgressPublisher |

### Webhook & Delivery

| Feature | Status | Notes |
|---------|--------|-------|
| Webhook callbacks | Shipped | `callback_url` with HTTPS + SSRF protection |
| Retry with backoff | Shipped | 3 attempts, exponential backoff |
| Callback failure tracking | Shipped | `CALLBACK_FAILED` job status |

### Security

| Feature | Status | Notes |
|---------|--------|-------|
| Input size limits | Shipped | 50 MB default |
| Zip bomb detection | Shipped | 500 MB uncompressed limit |
| XXE prevention | Shipped | defusedxml throughout |
| Encrypted PDF detection | Shipped | Clear error message |
| Credential redaction | Shipped | `_redact()` for all log output |
| SSRF protection | Shipped | Private IP rejection for webhook URLs |

---

## Infrastructure

| Feature | Status | Notes |
|---------|--------|-------|
| Docker Compose | Shipped | redis, api, worker-interactive, worker-batch, flower |
| Dockerfile | Shipped | Python 3.11-slim + LibreOffice + Tesseract |
| Settings consolidation | Shipped | All env vars in `settings.py` |
| CI workflow | Shipped | GitHub Actions, Python 3.10/3.11/3.12 |
| Traffic metrics export | Shipped | Nightly cron, append-only CSVs |

---

## Documentation

| Document | Status | Notes |
|----------|--------|-------|
| GitHub Pages landing | Shipped | `docs/index.html` — 19 formats, two-mode fork |
| Library quickstart | Shipped | `docs/quickstart-library.md` |
| Service quickstart | Shipped | `docs/quickstart-service.md` — Docker + local dev |
| API reference | Shipped | `docs/api-reference.md` — all endpoints |
| Configuration reference | Shipped | `docs/configuration.md` — all env vars |
| Architecture | Shipped | `docs/architecture.md` — pipeline, IR, deployment |
| Parser reference | Shipped | `docs/parsers.md` — per-format details |
| README | Shipped | Two-mode framing, all features documented |

---

## Planned / Not Yet Built

| Feature | Status | Notes |
|---------|--------|-------|
| distill-sdk (Python/Node client) | Planned | Requires versioned API prefix, auth, JSON schema |
| API versioning (`/api/v1/`) | Planned | Prerequisite for SDK |
| API key authentication | Planned | Prerequisite for hosted service |
| Scanned PDF demotion | Planned | Worker publishes `queue_demoted` event mid-job |
| Event replay for SSE | Planned | Currently no-replay; client gets final state only |
