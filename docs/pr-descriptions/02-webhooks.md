## What this PR does

Adds webhook callback delivery for async jobs. When a caller supplies a
`callback_url` with their conversion request, the worker POSTs the full result
to that URL when the job completes — no polling required.

## Files changed

App layer:
- `packages/app/distill_app/webhooks.py` — URL validation (HTTPS, SSRF protection), delivery with retry
- `packages/app/distill_app/worker.py` — callback delivery after job completion
- `packages/app/distill_app/jobs.py` — CALLBACK_FAILED status
- `packages/app/distill_app/server.py` — callback_url form field, validation at submission
- `packages/app/distill_app/settings.py` — WEBHOOK_TIMEOUT_SECONDS

Tests:
- `packages/app/tests/test_webhooks.py` — 27 tests covering URL validation, delivery, retry, API integration

## Tests

All 27 webhook tests pass. Full suite green.

## How to verify

```bash
# Submit with callback URL
curl -X POST http://localhost:7860/api/convert \
  -F "file=@test.pdf" \
  -F "callback_url=https://your-server.com/hook"

# Invalid callback rejected
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:7860/api/convert \
  -F "file=@test.pdf" \
  -F "callback_url=http://localhost/hook"
# Expected: 422
```

## Merge order

Depends on: `feat/trivial-wins` (shares worker.py, jobs.py, server.py, settings.py).
