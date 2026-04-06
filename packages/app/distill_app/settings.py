"""
Distill runtime configuration.

All settings are read from environment variables.
To configure locally: copy .env.example to .env and fill in the values.
For production: set these variables in your container environment or secrets
manager. Never commit .env to version control.

Settings are grouped by feature area. Optional settings have safe defaults and
can be left unset if the corresponding feature is not used.
"""

import os

# ---------------------------------------------------------------------------
# Infrastructure — required for all deployments
# ---------------------------------------------------------------------------

# Redis connection URL. Used by both the API (job submission) and the Celery
# worker (job processing). Must point to the same Redis instance.
# Default works for local Docker Compose (redis service name).
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# File size threshold above which conversion is routed to the async job queue.
# Documents below this size are converted synchronously.
ASYNC_SIZE_THRESHOLD_MB: int = int(
    os.getenv("DISTILL_ASYNC_SIZE_THRESHOLD_MB", "10")
)

# How long (seconds) job results are kept in Redis before expiring.
# Clients must poll GET /jobs/{id} within this window.
JOB_TTL_SECONDS: int = int(os.getenv("DISTILL_JOB_TTL_SECONDS", "3600"))

# Number of concurrent Celery worker processes per container.
WORKER_CONCURRENCY: int = int(os.getenv("DISTILL_WORKER_CONCURRENCY", "4"))

# Concurrency for the interactive worker pool (human-waiting jobs).
INTERACTIVE_CONCURRENCY: int = int(
    os.getenv("DISTILL_INTERACTIVE_CONCURRENCY", "4")
)

# Concurrency for the batch worker pool (background/large jobs).
BATCH_CONCURRENCY: int = int(os.getenv("DISTILL_BATCH_CONCURRENCY", "2"))

# ---------------------------------------------------------------------------
# LLM features — optional, required only for:
#   - Cross-page table merging  (llm_merge_tables=true in API request)
#   - Structured JSON extraction (extract=true in API request)
#   - Audio topic segmentation   (topic_segmentation=true in API request)
#
# These are server-side defaults. Per-request llm_api_key values take
# precedence if supplied by the caller. Leave blank to require callers to
# always supply their own key.
#
# Any OpenAI-compatible endpoint is supported. Set LLM_BASE_URL to use a
# self-hosted or third-party provider (e.g. Ollama, Azure OpenAI).
# ---------------------------------------------------------------------------

LLM_API_KEY: str = os.getenv("DISTILL_LLM_API_KEY", "")
LLM_MODEL: str = os.getenv("DISTILL_LLM_MODEL", "")
LLM_BASE_URL: str = os.getenv("DISTILL_LLM_BASE_URL", "")

# ---------------------------------------------------------------------------
# Vision / image captioning — optional, required only when images="caption"
# is set in ParseOptions.
#
# VISION_PROVIDER controls which backend is used:
#   openai     — requires DISTILL_VISION_API_KEY (OpenAI key)
#   anthropic  — requires DISTILL_VISION_API_KEY (Anthropic key)
#   ollama     — no key required; set DISTILL_VISION_BASE_URL to Ollama URL
# ---------------------------------------------------------------------------

VISION_PROVIDER: str = os.getenv("DISTILL_VISION_PROVIDER", "")
VISION_API_KEY: str = os.getenv("DISTILL_VISION_API_KEY", "")
VISION_BASE_URL: str = os.getenv("DISTILL_VISION_BASE_URL", "")

# ---------------------------------------------------------------------------
# Google Workspace — optional, required only for Google Docs/Sheets/Slides
# input via Google Drive URL.
#
# Two authentication methods are supported (mutually exclusive):
#   1. Per-request OAuth2 token — caller passes access_token in the API
#      request. No server-side config needed.
#   2. Service account — set DISTILL_GOOGLE_CREDENTIALS_PATH to the path of
#      a service account JSON key file. The server uses this for all Google
#      Drive requests.
#
# DISTILL_GOOGLE_ACCESS_TOKEN is a server-side default OAuth2 token.
# Per-request tokens supplied by callers always take precedence.
# ---------------------------------------------------------------------------

GOOGLE_ACCESS_TOKEN: str = os.getenv("DISTILL_GOOGLE_ACCESS_TOKEN", "")
GOOGLE_CREDENTIALS_PATH: str = os.getenv("DISTILL_GOOGLE_CREDENTIALS_PATH", "")

# ---------------------------------------------------------------------------
# Audio pipeline — optional, required only when processing audio files.
# Install the audio extras first: pip install distill-core[audio]
#
# HF_TOKEN is a Hugging Face access token, required to download the
# pyannote.audio speaker diarization model.
#
# To obtain a token:
#   1. Create a free account at https://huggingface.co
#   2. Accept the model licence at:
#      https://huggingface.co/pyannote/speaker-diarization-3.1
#   3. Generate an access token at https://huggingface.co/settings/tokens
#   4. Set DISTILL_HF_TOKEN to that token value.
#
# If HF_TOKEN is not set, speaker diarization is skipped and transcripts
# are produced without speaker labels. Transcription still works normally.
# ---------------------------------------------------------------------------

HF_TOKEN: str = os.getenv("DISTILL_HF_TOKEN", "")

# ---------------------------------------------------------------------------
# Webhook delivery — controls callback timeout for async job notifications
# ---------------------------------------------------------------------------

# Timeout (seconds) for each webhook delivery attempt to the caller's URL.
WEBHOOK_TIMEOUT_SECONDS: int = int(
    os.getenv("DISTILL_WEBHOOK_TIMEOUT_SECONDS", "10")
)

# ---------------------------------------------------------------------------
# SSE progress streaming — controls job progress event streaming
# ---------------------------------------------------------------------------

# Seconds between heartbeat comments when no progress event arrives.
# Keeps proxies (Nginx, AWS ALB) from closing idle SSE connections.
SSE_KEEPALIVE_SECONDS: int = int(
    os.getenv("DISTILL_SSE_KEEPALIVE_SECONDS", "15")
)

# Maximum duration (seconds) for an SSE stream before auto-closing.
SSE_MAX_DURATION_SECONDS: int = int(
    os.getenv("DISTILL_SSE_MAX_DURATION_SECONDS", "3600")
)
