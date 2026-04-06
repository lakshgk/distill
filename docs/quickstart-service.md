# Running Distill as a self-hosted service

This guide walks through deploying Distill as a containerised service using
Docker Compose. For using Distill as a Python library without the server
infrastructure, see [quickstart-library.md](quickstart-library.md).

---

## Prerequisites

- **Docker** (v20.10+) and **Docker Compose** (v2.0+)
- A machine with at least 2 GB of free RAM (more for audio processing)

---

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/nicobailon/distill.git
cd distill
```

### 2. Create your environment file

```bash
cp .env.example .env
```

### 3. Fill in configuration values

Open `.env` in your editor. The only variable required for a basic deployment is
`REDIS_URL`, which already has a working default for Docker Compose
(`redis://redis:6379/0`).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `REDIS_URL` | Yes | `redis://redis:6379/0` | Redis connection for job queue and result store |
| `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | No | `10` | Files above this size (MB) are routed to the async queue |
| `DISTILL_JOB_TTL_SECONDS` | No | `3600` | How long job results are kept in Redis (seconds) |
| `DISTILL_INTERACTIVE_CONCURRENCY` | No | `4` | Worker processes for the interactive queue |
| `DISTILL_BATCH_CONCURRENCY` | No | `2` | Worker processes for the batch queue |
| `DISTILL_LLM_API_KEY` | No | — | OpenAI-compatible API key for LLM features |
| `DISTILL_LLM_MODEL` | No | — | Model identifier (e.g. `gpt-4o`) |
| `DISTILL_HF_TOKEN` | No | — | Hugging Face token for speaker diarization |

All optional variables can be left empty. The system falls back to synchronous
processing and skips optional features (LLM, vision, Google Workspace, audio
diarization) when their credentials are not configured.

### 4. Start the services

```bash
docker compose up -d
```

### 5. Verify the deployment

```bash
curl http://localhost:7860/
```

A successful response serves the Distill web UI (HTML). If you get a connection
error, wait a few seconds for the containers to finish starting and try again.

---

## Services

The `docker-compose.yml` defines five services:

| Service | Role | Port |
|---|---|---|
| `redis` | Message broker and job result store | 6379 (internal) |
| `api` | FastAPI server — accepts conversion requests | **7860** |
| `worker-interactive` | Celery worker processing the `distill.interactive` queue | — |
| `worker-batch` | Celery worker processing the `distill.batch` queue | — |
| `flower` | Celery monitoring dashboard | **5555** |

The `api` service depends on `redis` being healthy before starting. Both worker
services also wait for a healthy Redis before accepting jobs. Flower starts after
at least one worker of each type is running.

---

## Converting a document

Submit a file to the convert endpoint:

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@report.docx" \
  -F "output_format=markdown"
```

The response is a JSON envelope containing the converted content and quality
metrics:

```json
{
  "markdown": "# Report Title\n\nContent here...",
  "quality": {
    "overall": 0.92,
    "headings": 1.0,
    "tables": 0.85,
    "lists": 0.9,
    "efficiency": 0.95
  },
  "stats": {
    "words": 1430,
    "pages": 5,
    "format": "DOCX"
  },
  "warnings": []
}
```

Supported `output_format` values: `markdown`, `json`, `html`, `chunks`.

---

## Async jobs

Not every conversion happens synchronously. A request is processed
asynchronously when:

- The file size exceeds `DISTILL_ASYNC_SIZE_THRESHOLD_MB` (default: 10 MB)
- The input is an audio file (audio is always async)

When a request goes async, the API returns HTTP 202 with a job ID:

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "queued",
  "poll_url": "/jobs/a1b2c3d4-...",
  "queue": "distill.interactive"
}
```

### Polling for results

```bash
curl http://localhost:7860/jobs/{job_id}
```

The response includes `status` (`queued`, `running`, `completed`, `failed`) and,
when complete, the full conversion `result`.

### Webhook callbacks

To receive a callback when a job finishes instead of polling, pass
`callback_url` in the convert request:

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@large-report.pdf" \
  -F "callback_url=https://example.com/hooks/distill"
```

Distill will POST the job result to the provided URL when conversion completes
or fails. The webhook timeout is controlled by `DISTILL_WEBHOOK_TIMEOUT_SECONDS`
(default: 10 seconds).

---

## Job priority

Distill separates work into two queues to prevent large batch jobs from blocking
interactive users:

| Queue | Worker | Use case |
|---|---|---|
| `distill.interactive` | `worker-interactive` | Small files, human-waiting requests |
| `distill.batch` | `worker-batch` | Large files, background processing |

### Auto-routing rules

1. If the caller passes `priority=interactive` or `priority=batch`, that queue
   is used directly.
2. Audio files are always routed to `distill.batch` regardless of size.
3. Files larger than `DISTILL_ASYNC_SIZE_THRESHOLD_MB` are routed to
   `distill.batch`.
4. Everything else goes to `distill.interactive`.

### Explicit priority

```bash
curl -X POST http://localhost:7860/api/convert \
  -F "file=@report.docx" \
  -F "priority=batch"
```

---

## Monitoring

Flower provides a web dashboard for monitoring Celery workers, active tasks, and
queue depths:

```
http://localhost:5555
```

Use it to verify that both `worker-interactive` and `worker-batch` are online
and processing jobs.

---

## Real-time progress

For long-running jobs, subscribe to a Server-Sent Events (SSE) stream to
receive progress updates in real time.

### With curl

```bash
curl -N http://localhost:7860/jobs/{job_id}/stream
```

### With JavaScript (EventSource)

```javascript
const source = new EventSource("/jobs/{job_id}/stream");

source.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`Status: ${data.status}, Progress: ${data.pct}%`);

  if (data.status === "completed" || data.status === "failed") {
    source.close();
  }
};
```

The stream emits JSON objects with `status`, `pct` (percentage), and a
timestamp. Heartbeat comments (`: heartbeat`) are sent every
`DISTILL_SSE_KEEPALIVE_SECONDS` (default: 15) to keep proxy connections alive.
The stream auto-closes after `DISTILL_SSE_MAX_DURATION_SECONDS` (default: 3600).

---

## Configuration

For a complete reference of all environment variables, defaults, and feature
toggles, see [configuration.md](configuration.md).

Key variables to be aware of:

| Variable | Default | Notes |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Must point to the same Redis instance for API and workers |
| `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `10` | Controls when sync/async routing switches over |
| `DISTILL_INTERACTIVE_CONCURRENCY` | `4` | Worker processes for the interactive queue |
| `DISTILL_BATCH_CONCURRENCY` | `2` | Worker processes for the batch queue |

---

## Scaling

### Adjusting concurrency

Worker concurrency is controlled by environment variables:

- `DISTILL_INTERACTIVE_CONCURRENCY` — number of processes per interactive worker
  container (default: 4)
- `DISTILL_BATCH_CONCURRENCY` — number of processes per batch worker container
  (default: 2)

Set these in your `.env` file and restart the workers.

### Scaling worker containers

To run multiple worker containers for a given queue:

```bash
docker compose up -d --scale worker-interactive=4
docker compose up -d --scale worker-batch=3
```

Each container runs the number of processes defined by its concurrency variable.
For example, scaling `worker-interactive` to 4 containers with a concurrency of
4 gives 16 total interactive worker processes.

---

## Troubleshooting

### LibreOffice not found

Some document formats (`.doc`, `.odt`, `.ppt`, `.xls`) require LibreOffice for
conversion. If you see LibreOffice-related errors, verify that the Docker image
includes LibreOffice. The Distill Dockerfile should install it automatically. If
you are running outside Docker, install LibreOffice and ensure `soffice` is on
your `PATH`.

### Redis not reachable

Symptoms: the API returns HTTP 503, jobs never start, or the async response
includes `X-Distill-Async: degraded` (meaning the API fell back to synchronous
processing because it could not reach Redis).

Check that Redis is running:

```bash
docker compose ps redis
docker compose logs redis
```

Verify the `REDIS_URL` in your `.env` matches the Redis service name in
`docker-compose.yml` (`redis://redis:6379/0` for the default setup).

### Job stuck in "queued" or "running"

1. Open Flower at `http://localhost:5555` and check whether workers are online.
2. Verify the correct worker is listening on the expected queue — interactive
   jobs require `worker-interactive`, batch jobs require `worker-batch`.
3. Check worker logs for errors:

```bash
docker compose logs worker-interactive
docker compose logs worker-batch
```

4. If a worker crashed mid-job, the job may remain in a non-terminal state until
   `DISTILL_JOB_TTL_SECONDS` (default: 3600) expires and Redis evicts the key.
   Restarting the worker will not automatically retry the stuck job — submit the
   file again.
