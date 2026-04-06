## What this PR does

Adds OpenDocument Text (.odt) and macro-enabled Excel (.xlsm) format support,
a production-ready Docker Compose stack with Flower monitoring, and the
Dockerfile for containerised deployment.

## Files changed

Parsers:
- `packages/core/distill/parsers/docx.py` — OdtParser for .odt via LibreOffice
- `packages/core/distill/parsers/xlsx.py` — .xlsm support with macro stripping

Infrastructure:
- `docker-compose.yml` — redis, api, worker-interactive, worker-batch, flower
- `Dockerfile` — Python 3.11-slim with LibreOffice and Tesseract
- `.env.example` — all environment variables with defaults and comments

Tests:
- `packages/core/tests/test_xlsx.py` — XLSM tests added

## Tests

Full suite passes (679 passed, 1 skipped). The 1 skipped test is
`test_native_pdf_does_not_trigger_ocr` (expected — OCR dependencies not installed).

## How to verify

```bash
docker compose up -d
docker compose ps   # all 5 services running
curl -s http://localhost:7860/ | head -1   # HTML response
```

## Merge order

First PR — no dependencies.
