# Configuration Reference

Single source of truth for all Distill environment variables.

## How configuration works

- All settings live in `packages/app/distill_app/settings.py` and are read from
  environment variables at startup.
- For local development, copy `.env.example` to `.env` and fill in values.
  `.env` is gitignored — never commit it to version control.
- For production, set variables in your container environment or secrets manager.
- Variables left empty disable the corresponding feature gracefully. The system
  falls back to synchronous processing and skips optional features (LLM, vision,
  Google Workspace, audio diarization) when their credentials are not configured.

---

## Variable reference

### Infrastructure

Required for all deployments.

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `REDIS_URL` | `str` | `redis://localhost:6379/0` | **Yes (production)** | API + Worker | Redis connection URL used by the API server (job submission), Celery worker (job processing), and the job store. Must point to the same Redis instance for both services. |
| `DISTILL_ASYNC_SIZE_THRESHOLD_MB` | `int` | `10` | No | API | File size threshold (MB) above which conversion is routed to the async Celery job queue. Documents below this size are converted synchronously. |
| `DISTILL_JOB_TTL_SECONDS` | `int` | `3600` | No | Worker | How long (seconds) job results are kept in Redis before expiring. Clients must poll `GET /jobs/{id}` within this window. |
| `DISTILL_WORKER_CONCURRENCY` | `int` | `4` | No | Worker | Number of concurrent Celery worker processes per container. |

### Queue routing

Controls concurrency for the two worker pools.

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_INTERACTIVE_CONCURRENCY` | `int` | `4` | No | Worker | Concurrency for the interactive worker pool (human-waiting, latency-sensitive jobs). |
| `DISTILL_BATCH_CONCURRENCY` | `int` | `2` | No | Worker | Concurrency for the batch worker pool (background/large jobs). |

### Webhooks

Controls callback delivery for async job notifications.

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_WEBHOOK_TIMEOUT_SECONDS` | `int` | `10` | No | Worker | Timeout (seconds) for each webhook delivery attempt to the caller's callback URL. |

### SSE streaming

Controls job progress event streaming over Server-Sent Events.

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_SSE_KEEPALIVE_SECONDS` | `int` | `15` | No | API | Seconds between heartbeat comments when no progress event arrives. Keeps proxies (Nginx, AWS ALB) from closing idle SSE connections. |
| `DISTILL_SSE_MAX_DURATION_SECONDS` | `int` | `3600` | No | API | Maximum duration (seconds) for an SSE stream before the server auto-closes it. |

### LLM features

Optional. Required only when using LLM-powered features:
- Cross-page table merging (`llm_merge_tables=true` in API request)
- Structured JSON extraction (`extract=true` in API request)
- Audio topic segmentation (`topic_segmentation=true` in API request)

Per-request `llm_api_key` values supplied by the caller take precedence over these
server-side defaults. Any OpenAI-compatible endpoint is supported — set
`DISTILL_LLM_BASE_URL` to use a self-hosted or third-party provider (e.g. Ollama,
Azure OpenAI).

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_LLM_API_KEY` | `str` | `""` | No | Worker | API key for the LLM endpoint. Leave blank to require callers to always supply their own key per request. |
| `DISTILL_LLM_MODEL` | `str` | `""` | No | Worker | Model identifier (e.g. `gpt-4o`, `claude-3-5-sonnet`, `mistral-small`). |
| `DISTILL_LLM_BASE_URL` | `str` | `""` | No | Worker | Base URL for the LLM endpoint (e.g. `https://api.openai.com/v1`). Leave empty to require callers to supply `base_url` per request. |

### Vision

Optional. Required only when `images="caption"` is set in `ParseOptions`.

`DISTILL_VISION_PROVIDER` controls which backend is used:
- `openai` — requires `DISTILL_VISION_API_KEY` (OpenAI key)
- `anthropic` — requires `DISTILL_VISION_API_KEY` (Anthropic key)
- `ollama` — no key required; set `DISTILL_VISION_BASE_URL` to the Ollama URL

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_VISION_PROVIDER` | `str` | `""` | No | Worker | Vision backend to use: `openai`, `anthropic`, or `ollama`. |
| `DISTILL_VISION_API_KEY` | `str` | `""` | No | Worker | API key for the vision provider. Not needed when provider is `ollama`. |
| `DISTILL_VISION_BASE_URL` | `str` | `""` | No | Worker | Base URL for Ollama or a custom vision endpoint. |

### Google Workspace

Optional. Required only for Google Docs, Sheets, and Slides input via Google Drive URL.

Two authentication methods are supported (mutually exclusive):
1. **Per-request OAuth2 token** — caller passes `access_token` in the API request.
   No server-side configuration needed.
2. **Service account** — set `DISTILL_GOOGLE_CREDENTIALS_PATH` to the path of a
   service account JSON key file. The server uses this for all Google Drive requests.

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_GOOGLE_ACCESS_TOKEN` | `str` | `""` | No | Worker | Server-side default OAuth2 token. Per-request tokens supplied by callers always take precedence. |
| `DISTILL_GOOGLE_CREDENTIALS_PATH` | `str` | `""` | No | Worker | Path to a Google service account JSON key file for server-wide authentication. |

### Audio

Optional. Required only when processing audio files with speaker diarization.
Install the audio extras first: `pip install distill-core[audio]`.

If `DISTILL_HF_TOKEN` is not set, speaker diarization is skipped and transcripts
are produced without speaker labels. Transcription itself still works normally.

To obtain a Hugging Face token:
1. Create a free account at <https://huggingface.co>
2. Accept the model licence at <https://huggingface.co/pyannote/speaker-diarization-3.1>
3. Generate an access token at <https://huggingface.co/settings/tokens>

| Variable | Type | Default | Required | Services | Description |
|---|---|---|---|---|---|
| `DISTILL_HF_TOKEN` | `str` | `""` | No | Worker | Hugging Face access token for downloading the pyannote speaker diarization model. |

---

## Security notes

The following variables contain secrets and must never appear in logs:

- `DISTILL_LLM_API_KEY`
- `DISTILL_VISION_API_KEY`
- `DISTILL_GOOGLE_ACCESS_TOKEN`
- `DISTILL_HF_TOKEN`

All logging in `distill_app` passes these through `_redact()` before output.
