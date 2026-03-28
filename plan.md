# Distill — Implementation Plan

_Last updated: 2026-03-27_
_Based on: Distill_Product_Spec.docx v1.0 (March 2026)_

---

## Deployment Target

Distill ships in three forms:

| Distribution | Package | Audience |
|---|---|---|
| PyPI library | `pip install distill-core` | Python developers; covers .docx/.xlsx/.pptx/.pdf/Google with no system deps |
| PyPI desktop app | `pip install distill-app` | End users; local Gradio UI wrapping distill-core |
| Docker image | `docker run distill` | Users needing legacy format support (.doc/.xls/.ppt); LibreOffice pre-bundled, runs locally |

**No cloud service. No Celery + Redis. No async job queue.**
Docker is a local convenience bundle — it solves the LibreOffice installation problem for legacy formats without requiring users to install LibreOffice themselves.

---

## Decisions Log

| # | Question | Decision |
|---|---|---|
| Q1 | Image extraction in v1 | Alt-text + captions inline only — no sidecar files |
| Q2 | Token counting for quality score | Skip — quality score uses structural metrics only (no LLM dependency) |
| Q3 | PyMuPDF (AGPL-3.0) | Skip — use pdfplumber only, keeps distill-core fully MIT-licensed |
| Q4 | LibreOffice pool size | Configurable via env var, default 3 — applies to local legacy format conversion only |
| Q5 | Async job queue | Not needed — local library, no concurrent service to manage |
| Q8 | Distribution targets | pip (distill-core + distill-app) AND Docker image (LibreOffice pre-bundled for legacy formats) |
| Q6 | Metadata in output | Always included by default; suppressible via `include_metadata=False` |
| Q7 | Metadata fields | title, author, created, modified, subject, description, keywords, page/slide/sheet count, source format |

---

## Current State (as of plan creation)

| Layer | Status | Notes |
|---|---|---|
| IR (`ir.py`) | ✅ Done | Document tree dataclasses; `DocumentMetadata` exists |
| Registry (`registry.py`) | ✅ Done | Parser registration |
| Renderer (`renderer.py`) | ✅ Done | IR → CommonMark/GFM; `front_matter` flag exists but optional |
| Quality scorer (`quality.py`) | ✅ Done | Structural metrics; token reduction metric not needed |
| Parser — DOCX | ✅ Exists | Needs mammoth + pandoc pipeline; metadata not fully populated |
| Parser — XLSX | ✅ Exists | Needs openpyxl + pandas + tabulate; metadata not fully populated |
| Parser — PPTX | ✅ Exists | Needs python-pptx pipeline; metadata not fully populated |
| Parser — PDF | ✅ Exists | Needs pdfplumber primary; metadata not fully populated |
| Parser — Google | ✅ Exists | Needs Drive API export pipeline; metadata not fully populated |
| Metadata capture | ❌ Inconsistent | Not enforced across parsers; front_matter defaults to off |
| Tests (`test_ir.py`) | ✅ Partial | IR + renderer + quality; no parser tests |
| Parser tests | ❌ Missing | No tests for any of the 5 parsers |
| Docs | ❌ Missing | `docs/architecture.md`, `docs/parsers.md` not created |
| Legacy formats (.doc/.xls/.ppt) | ❌ Missing | LibreOffice pre-processing not implemented |
| Scanned PDF (OCR) | ❌ Missing | docling + Tesseract pipeline not implemented |
| Security hardening | ❌ Missing | defusedxml, zip bomb protection, input validation |

---

## Metadata Requirement

**Every parser must populate all available metadata fields. Metadata is always output as YAML front matter.**

### Fields captured (all formats where available)

| Field | DOCX | XLSX | PPTX | PDF | Google |
|---|---|---|---|---|---|
| `title` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `author` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `created` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `modified` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `subject` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `description` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `keywords` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `page_count` | ✅ | — | — | ✅ | ✅ |
| `slide_count` | — | — | ✅ | — | ✅ (Slides) |
| `sheet_count` | — | ✅ | — | — | ✅ (Sheets) |
| `source_format` | ✅ | ✅ | ✅ | ✅ | ✅ |

### Output format

```markdown
---
title: "Q4 Financial Report"
author: "Jane Smith"
created: "2026-01-15"
modified: "2026-03-20"
subject: "Quarterly Results"
description: "Finance team Q4 summary"
keywords: ["finance", "quarterly", "2025"]
pages: 12
source_format: "docx"
---

# Introduction
...
```

### API surface change

`DocumentMetadata` in `ir.py` must be extended to include all fields above.
`MarkdownRenderer` must default `front_matter=True` (currently defaults to `False`).
`convert()` must accept `include_metadata=False` to suppress front matter.

---

## Phase 1 — DOCX + PDF Parser Upgrades + Metadata
**Target: 4 weeks | Exit criteria: DOCX + PDF quality score ≥ 0.85; metadata present in all outputs**

### What gets built
- Extend `DocumentMetadata` IR with full field set (subject, description, keywords, dates, counts, source_format)
- Default `front_matter=True` in renderer; `convert()` gains `include_metadata` param
- Upgrade DOCX parser: mammoth (primary body) + pandoc (fallback) + python-docx (metadata extraction)
- Upgrade PDF parser: pdfplumber primary; PDF type detection; header/footer suppression; metadata via pdfplumber
- Image handling: alt-text + captions inline; decorative images suppressed (already in IR)
- Security baseline: defusedxml for OOXML parsing, input size validation, zip bomb pre-check
- Test fixture files (DOCX + PDF samples covering edge cases)
- `tests/test_docx.py` + `tests/test_pdf.py`
- `docs/architecture.md` (initial) + `docs/parsers.md` (DOCX + PDF sections)

### Prompt sequence (4-prompt pattern)
- [ ] **1a** — Extend `DocumentMetadata` + update renderer default + `convert()` API + smoke test
- [ ] **1b** — Upgrade DOCX parser (mammoth + pandoc fallback + python-docx metadata) + smoke test
- [ ] **1c** — Upgrade PDF parser (pdfplumber + type detection + metadata + header/footer suppression) + smoke test
- [ ] **2** — Security baseline: defusedxml + input validation (applied to DOCX + PDF paths)
- [ ] **4** — `tests/test_docx.py` + `tests/test_pdf.py` (with fixture files)
- [ ] **V** — Verification: `pytest -v` passes; quality score ≥ 0.85 on DOCX + PDF samples; metadata present in output

### Files touched
`packages/core/distill/ir.py`,
`packages/core/distill/renderer.py`,
`packages/core/distill/__init__.py`,
`packages/core/distill/parsers/docx.py`,
`packages/core/distill/parsers/pdf.py`,
`packages/core/tests/test_docx.py` (new),
`packages/core/tests/test_pdf.py` (new),
`docs/architecture.md` (new),
`docs/parsers.md` (new)

---

## Phase 2 — XLSX + PPTX + Legacy Format Support
**Target: 3 weeks | Exit criteria: All P0 formats operational; token reduction ≥ 40% vs naive extraction**

### What gets built
- Upgrade XLSX parser: openpyxl + pandas + tabulate; merged cell handling; row cap (default 500); metadata
- Upgrade PPTX parser: python-pptx; speaker notes as blockquote; SmartArt text fallback; metadata
- LibreOffice pre-processing: .doc → .docx, .xls → .xlsx, .ppt → .pptx (local headless, process pool default 3, configurable via env var `DISTILL_LIBREOFFICE_WORKERS`)
- **Docker image**: `python:3.11-slim` + LibreOffice pre-installed; `distill-core` and `distill-app` bundled; published to Docker Hub as `distill/distill`
- Security: defusedxml applied to XLSX + PPTX paths
- `tests/test_xlsx.py` + `tests/test_pptx.py`
- Update `docs/parsers.md` (XLSX + PPTX sections)

### Prompt sequence
- [ ] **1a** — Upgrade XLSX parser + metadata + smoke test
- [ ] **1b** — Upgrade PPTX parser + metadata + smoke test
- [ ] **1c** — LibreOffice pool manager + legacy format pre-processing + smoke test
- [ ] **1d** — `Dockerfile` + `docker-compose.yml`; verify `docker build` succeeds and legacy conversion works inside container
- [ ] **4** — `tests/test_xlsx.py` + `tests/test_pptx.py`
- [ ] **V** — Verification: `pytest -v` passes; all P0 formats produce metadata; Docker image builds and runs correctly

### Files touched
`packages/core/distill/parsers/xlsx.py`,
`packages/core/distill/parsers/pptx.py`,
`packages/core/distill/parsers/` (new `libreoffice.py` pool manager),
`packages/core/tests/test_xlsx.py` (new),
`packages/core/tests/test_pptx.py` (new),
`docs/parsers.md`

---

## Phase 3 — Google Workspace Integration
**Target: 3 weeks | Exit criteria: Google Docs (P0) ≥ 0.85; Sheets + Slides ≥ 0.70**

_OAuth strategy: stateless — caller provides access_token; Distill does not store or refresh credentials._

### What gets built
- Google Drive export client (google-api-python-client)
- Google Docs → DOCX export → DOCX pipeline (primary); native MD export where complete
- Google Sheets → XLSX export → XLSX pipeline
- Google Slides → PPTX export → PPTX pipeline
- Metadata: Drive API supplies title, author, created/modified dates
- `tests/test_google.py` (mocked Drive API)
- Update `docs/parsers.md` (Google section)

### Prompt sequence
- [ ] **1** — Google Drive client + export service + metadata extraction + smoke test
- [ ] **4** — `tests/test_google.py` with mocked Drive API responses
- [ ] **V** — Verification: `pytest -v` passes; Google Docs quality ≥ 0.85

### Files touched
`packages/core/distill/parsers/google.py`,
`packages/core/tests/test_google.py` (new),
`docs/parsers.md`

---

## Phase 4 — Scanned PDF (OCR)
**Target: 4 weeks | Exit criteria: Scanned PDF quality score ≥ 0.70**

### What gets built
- Scanned PDF detection (pdfplumber character count threshold)
- docling pipeline (default OCR path)
- Tesseract + pdf2image (lightweight mode, `distill-core[ocr]` optional extra)
- Per-page type detection for mixed scanned + native PDFs
- Metadata extraction for scanned PDFs (from pdfplumber document info)
- Expand test corpus (scanned PDF samples)
- `tests/test_pdf_scanned.py`
- Update `docs/parsers.md` (PDF scanned section)

### Prompt sequence
- [ ] **1a** — Scanned PDF detection logic + docling integration + smoke test
- [ ] **1b** — Tesseract/pdf2image lightweight fallback
- [ ] **1c** — Per-page type detection (mixed PDFs)
- [ ] **4** — `tests/test_pdf_scanned.py`
- [ ] **V** — Verification: `pytest -v` passes; scanned PDF quality ≥ 0.70

### Files touched
`packages/core/distill/parsers/pdf.py`,
`packages/core/pyproject.toml` (new `[ocr]` extra),
`packages/core/tests/test_pdf_scanned.py` (new),
`docs/parsers.md`

---

## Phase 5 — Hardening + Test Corpus
**Target: 3 weeks | Exit criteria: Penetration-style edge case tests pass; 200-doc corpus CI gate in place**

### What gets built
- Security audit: defusedxml consistently applied; zip bomb protection; password-protected PDF returns clear error
- Edge case corpus (200 documents across all formats covering: nested tables, tracked changes, SmartArt, merged cells, multi-column PDFs, malformed files, oversized inputs)
- CI quality regression gate: 5% mean score drop per format blocks merge
- Plain text / HTML / RTF support (P2 — nice to have, if time allows)
- `docs/architecture.md` — security section added
- `README.md` updated with full public API surface

### Prompt sequence
- [ ] **1** — Security controls audit + any missing defusedxml / size checks
- [ ] **2** — Edge case test suite (`tests/test_edge_cases.py`)
- [ ] **3** — CI corpus job (GitHub Actions quality regression gate)
- [ ] **V** — All tests pass; CI gate fires correctly on a deliberately degraded parser

### Files touched
All parser files (security audit),
`packages/core/tests/test_edge_cases.py` (new),
`.github/workflows/ci.yml`,
`docs/architecture.md`,
`README.md`

### Also in Phase 5: publish to PyPI + Docker Hub
- [ ] PyPI release: `distill-core` + `distill-app` published via `twine`
- [ ] Docker Hub: `docker push distill/distill:latest` + versioned tag
- [ ] `README.md` updated with install instructions for both paths

---

## Definition of Done (per CLAUDE.md)

A phase is complete when:
1. All verification commands (`pytest -v`) pass and output has been shown
2. `docs/architecture.md` updated with any structural changes
3. `docs/parsers.md` updated for any new/changed parser behaviour
4. `README.md` updated if public API surface (`convert()`, `convert_to_ir()`, `ParseOptions`) changes
5. Step marked complete only after test output is shown — not when code looks correct

---

## Dependency Map

```
Phase 1 (IR + renderer + DOCX + PDF + metadata baseline)
  └── Phase 2 (XLSX + PPTX + LibreOffice legacy)
        └── Phase 3 (Google Workspace)
              └── Phase 4 (Scanned PDF OCR)
                    └── Phase 5 (Hardening + corpus CI)
```
