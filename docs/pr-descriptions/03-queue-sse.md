## What this PR does

Adds priority queue routing (interactive vs batch) and real-time job progress
streaming via Server-Sent Events. Interactive jobs get their own worker pool
that batch traffic can never starve.

## Files changed

App layer:
- `packages/app/distill_app/queues.py` — queue constants, route_job() routing logic
- `packages/app/distill_app/progress.py` — ProgressPublisher, ProgressEvent, Redis pub/sub
- `packages/core/distill/parsers/audio.py` — progress instrumentation at audio pipeline stages

Tests:
- `packages/app/tests/test_queues.py` — 16 tests for routing logic, API, worker
- `packages/app/tests/test_sse.py` — 19 tests for publisher, worker instrumentation, SSE endpoint

## Tests

All 35 new tests pass. Full suite green.

## How to verify

```bash
# Invalid priority rejected
curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:7860/api/convert \
  -F "file=@test.pdf" -F "priority=urgent"
# Expected: 422

# SSE stream for unknown job returns 404
curl -s -o /dev/null -w "%{http_code}" http://localhost:7860/jobs/nonexistent/stream
# Expected: 404
```

## Merge order

Depends on: `feat/webhooks` (shares worker.py, server.py).
