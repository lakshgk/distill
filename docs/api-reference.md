# Distill REST API Reference

Base URL: `http://localhost:7860`

---

## POST /api/convert

Convert an uploaded document to the requested output format.

Small files are processed synchronously (HTTP 200). Large files, audio files,
and scanned PDFs are routed to an async job queue and return HTTP 202 with a
`job_id` for polling.

### Request

Content-Type: `multipart/form-data`

| Parameter | Type | Default | Required | Description |
|---|---|---|---|---|
| `file` | file | — | Yes | The document to convert. Supported extensions: `.csv`, `.doc`, `.docx`, `.epub`, `.flac`, `.htm`, `.html`, `.json`, `.m4a`, `.mp3`, `.odt`, `.ogg`, `.pdf`, `.ppt`, `.pptx`, `.sql`, `.wav`, `.wsd`, `.wsdl`, `.xls`, `.xlsm`, `.xlsx`. |
| `output_format` | string | `"markdown"` | No | Output format. One of: `markdown`, `chunks`, `json`, `html`. |
| `include_metadata` | boolean | `true` | No | Include document metadata (word count, page count, etc.) in the conversion. |
| `max_rows` | integer | `500` | No | Maximum number of table rows to include per table. |
| `enable_ocr` | boolean | `false` | No | Enable OCR for scanned pages. |
| `extract_content` | boolean | `false` | No | Enable content extraction mode. |
| `llm_merge_tables` | boolean | `false` | No | Use an LLM to merge split tables. Requires `llm_api_key` and `llm_model`. |
| `llm_api_key` | string | `""` | No | API key for LLM features. Falls back to the `DISTILL_LLM_API_KEY` environment variable. |
| `llm_model` | string | `""` | No | LLM model identifier. Falls back to the `DISTILL_LLM_MODEL` environment variable. |
| `extract` | boolean | `false` | No | Enable structured data extraction. Requires `schema`, `llm_api_key`, and `llm_model`. |
| `schema` | string | `""` | No | JSON object defining the extraction schema. Required when `extract=true`. Must be a non-empty JSON object. |
| `transcription_engine` | string | `"whisper"` | No | Transcription engine for audio files. One of: `whisper`, `vosk`. |
| `whisper_model` | string | `"base"` | No | Whisper model size. |
| `hf_token` | string | `""` | No | Hugging Face token for gated model access. Falls back to the `HF_TOKEN` environment variable. |
| `topic_segmentation` | boolean | `false` | No | Enable topic segmentation for audio transcriptions. Requires an LLM API key. |
| `callback_url` | string | `null` | No | HTTPS URL to receive the job result via webhook POST when async processing completes. Must use HTTPS and must not target private/reserved IP ranges. |
| `priority` | string | `null` | No | Queue priority override. One of: `interactive`, `batch`. When omitted, routing is automatic based on file size and type. |

### Synchronous Response (200 OK)

Returned when the file is small enough for inline processing and Redis is unavailable or the request does not meet async thresholds.

#### output_format = "markdown" (default)

```json
{
  "markdown": "# Document Title\n\nBody text...",
  "quality": {
    "overall": 0.912,
    "headings": 1.0,
    "tables": 0.85,
    "lists": 0.9,
    "efficiency": 0.897
  },
  "stats": {
    "words": 1240,
    "pages": 3,
    "slides": null,
    "sheets": null,
    "format": "DOCX"
  },
  "warnings": [
    {
      "type": "table_truncated",
      "message": "Table in section 2 exceeded max_rows (500); truncated."
    }
  ]
}
```

#### output_format = "chunks"

```json
{
  "chunks": [
    {
      "heading": "Introduction",
      "content": "This document covers...",
      "token_estimate": 84
    }
  ],
  "chunk_count": 12,
  "quality": { "overall": 0.912, "headings": 1.0, "tables": 0.85, "lists": 0.9, "efficiency": 0.897 },
  "stats": { "words": 1240, "pages": 3, "slides": null, "sheets": null, "format": "DOCX" },
  "warnings": []
}
```

#### output_format = "json"

```json
{
  "document": {
    "title": "Document Title",
    "sections": []
  },
  "quality": { "overall": 0.912, "headings": 1.0, "tables": 0.85, "lists": 0.9, "efficiency": 0.897 },
  "stats": { "words": 1240, "pages": 3, "slides": null, "sheets": null, "format": "DOCX" },
  "warnings": []
}
```

#### output_format = "html"

```json
{
  "html": "<h1>Document Title</h1><p>Body text...</p>",
  "quality": { "overall": 0.912, "headings": 1.0, "tables": 0.85, "lists": 0.9, "efficiency": 0.897 },
  "stats": { "words": 1240, "pages": 3, "slides": null, "sheets": null, "format": "DOCX" },
  "warnings": []
}
```

#### With extract = true

When `extract=true` and extraction succeeds, the response includes an additional `extracted` field:

```json
{
  "markdown": "...",
  "quality": { "overall": 0.912 },
  "stats": { "words": 1240, "pages": 3, "slides": null, "sheets": null, "format": "PDF" },
  "warnings": [],
  "extracted": {
    "invoice_number": "INV-2026-0042",
    "total": 1599.00
  }
}
```

### Asynchronous Response (202 Accepted)

Returned when the file exceeds the async size threshold, is an audio file, or
is a scanned PDF. The client should poll `GET /jobs/{job_id}` or connect to
`GET /jobs/{job_id}/stream` for real-time progress.

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued",
  "poll_url": "/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "queue": "distill.interactive"
}
```

### Response Objects

#### quality

| Field | Type | Description |
|---|---|---|
| `overall` | float or null | Overall quality score (0.0 to 1.0). Null when scoring fails. |
| `headings` | float | Heading preservation score (0.0 to 1.0). |
| `tables` | float | Table preservation score (0.0 to 1.0). |
| `lists` | float | List preservation score (0.0 to 1.0). |
| `efficiency` | float | Token reduction ratio (0.0 to 1.0). |
| `error` | string | Present only when `overall` is null. Describes why scoring failed. |

#### warnings (array)

Each element in the `warnings` array is an object:

| Field | Type | Description |
|---|---|---|
| `type` | string | Machine-readable warning category (e.g. `table_truncated`, `image_skipped`, `ocr_fallback`). |
| `message` | string | Human-readable description of the warning. |

#### stats

| Field | Type | Description |
|---|---|---|
| `words` | integer or null | Word count of the source document. |
| `pages` | integer or null | Page count (PDF, DOCX). |
| `slides` | integer or null | Slide count (PPTX). |
| `sheets` | integer or null | Sheet count (XLSX). |
| `format` | string or null | Source format in uppercase (e.g. `"DOCX"`, `"PDF"`). |

### Error Responses

| Status | Condition | Example detail |
|---|---|---|
| 400 | Unsupported file extension | `"Unsupported format: '.bmp'. Supported: .csv, .doc, ..."` |
| 422 | Invalid `output_format` | `"Invalid output_format 'xml'. Accepted: chunks, html, json, markdown"` |
| 422 | Invalid `priority` | `"priority must be 'interactive' or 'batch'"` |
| 422 | Invalid `callback_url` | `"callback_url must use https"` |
| 422 | `extract=true` without schema | `"extract=True requires a non-empty schema"` |
| 422 | `extract=true` with invalid JSON schema | `"schema is not valid JSON: ..."` |
| 422 | `llm_merge_tables=true` without API key | `"llm_merge_tables requires llm_api_key and llm_model to be set"` |
| 422 | Audio with unsupported output format | `"Audio input does not support output_format='json'. Supported for audio: markdown, chunks."` |
| 422 | Audio with unsupported transcription engine | `"Unknown transcription_engine: 'deepgram'. Supported: whisper, vosk."` |
| 500 | Unexpected server error | `"Unexpected error: ..."` |
| 503 | Audio upload when Redis unavailable | `"Audio conversion requires async processing but the job queue is unavailable."` |

### Example: curl

```bash
# Basic markdown conversion
curl -X POST http://localhost:7860/api/convert \
  -F "file=@report.docx"

# Convert to chunks with OCR enabled
curl -X POST http://localhost:7860/api/convert \
  -F "file=@scanned.pdf" \
  -F "output_format=chunks" \
  -F "enable_ocr=true"

# Structured extraction with LLM
curl -X POST http://localhost:7860/api/convert \
  -F "file=@invoice.pdf" \
  -F "extract=true" \
  -F 'schema={"invoice_number": "string", "total": "number"}' \
  -F "llm_api_key=sk-..." \
  -F "llm_model=gpt-4o"

# Async with webhook callback
curl -X POST http://localhost:7860/api/convert \
  -F "file=@recording.mp3" \
  -F "callback_url=https://example.com/webhook" \
  -F "priority=batch"
```

---

## GET /jobs/{job_id}

Poll the status of an async conversion job.

### Path Parameters

| Parameter | Type | Description |
|---|---|---|
| `job_id` | string | The UUID returned by the async `POST /api/convert` response. |

### Response (200 OK)

The response shape depends on the job status.

#### Queued or Processing

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued",
  "queue": "distill.interactive"
}
```

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "processing",
  "queue": "distill.batch"
}
```

#### Complete

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "complete",
  "queue": "distill.interactive",
  "result": {
    "markdown": "# Converted Document\n\nContent...",
    "quality": { "overall": 0.912 },
    "stats": { "words": 1240, "pages": 3, "slides": null, "sheets": null, "format": "DOCX" },
    "warnings": []
  }
}
```

#### Failed

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "failed",
  "queue": "distill.batch",
  "error": "Parser raised ValueError: corrupted PDF stream"
}
```

### Job Statuses

| Status | Description |
|---|---|
| `queued` | Job is in the queue, waiting for a worker. |
| `processing` | A worker has picked up the job and conversion is in progress. |
| `complete` | Conversion finished successfully. `result` is populated. |
| `failed` | Conversion failed. `error` is populated. |
| `callback_failed` | Conversion completed but the webhook callback could not be delivered. |

### Error Responses

| Status | Condition | Detail |
|---|---|---|
| 404 | Job ID not found or expired | `"Job not found"` |
| 503 | Redis unavailable | `"Redis is unavailable"` |

### Example: curl

```bash
curl http://localhost:7860/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

---

## GET /jobs/{job_id}/stream

Subscribe to real-time progress updates for an async job via Server-Sent Events (SSE).

### Path Parameters

| Parameter | Type | Description |
|---|---|---|
| `job_id` | string | The UUID returned by the async `POST /api/convert` response. |

### Response

Content-Type: `text/event-stream`

The server sends SSE `data:` frames containing JSON objects. Each event
follows the `ProgressEvent` schema:

| Field | Type | Always present | Description |
|---|---|---|---|
| `job_id` | string | Yes | The job UUID. |
| `status` | string | Yes | Current status: `queued`, `processing`, `completed`, `failed`. |
| `queue` | string | Yes | Queue name the job is running on. |
| `ts` | string | Yes | ISO 8601 UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`). |
| `stage` | string | No | Current pipeline stage (see table below). |
| `pct` | integer | No | Estimated completion percentage (0-100). |
| `message` | string | No | Human-readable status message. |

### Pipeline Stages

| Stage | Approximate % Range | Description |
|---|---|---|
| `queued` | 0 | Job is waiting in the queue. |
| `parsing` | 5-40 | Format parser is reading the source document. |
| `building_ir` | 40-60 | Constructing the intermediate representation. |
| `rendering` | 60-80 | Rendering the IR to the requested output format. |
| `scoring` | 80-90 | Computing quality scores. |
| `complete` | 100 | Conversion finished. |

### Special Events

**Heartbeat:** When no progress events arrive within the keepalive interval
(default 15 seconds), the server sends an SSE comment line:

```
: heartbeat
```

**Timeout:** If the stream exceeds the maximum duration (default 1 hour), a
timeout event is sent and the stream closes:

```
data: {"status": "timeout", "message": "Stream duration limit reached"}
```

**Error:** If the Redis connection fails, an error event is sent:

```
data: {"status": "error", "message": "Progress stream unavailable"}
```

### Terminal Events

The stream closes automatically after receiving an event with `status` set to
`completed` or `failed`.

If the job has already reached a terminal status (`complete`, `failed`, or
`callback_failed`) when the SSE connection is opened, the server sends a single
final event and closes immediately.

### Example: curl

```bash
curl -N http://localhost:7860/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890/stream
```

Example event stream:

```
data: {"job_id": "a1b2c3d4-...", "status": "processing", "queue": "distill.interactive", "ts": "2026-04-04T12:00:01Z", "stage": "parsing", "pct": 10}

data: {"job_id": "a1b2c3d4-...", "status": "processing", "queue": "distill.interactive", "ts": "2026-04-04T12:00:03Z", "stage": "rendering", "pct": 65}

data: {"job_id": "a1b2c3d4-...", "status": "completed", "queue": "distill.interactive", "ts": "2026-04-04T12:00:05Z", "pct": 100}
```

### Error Responses

| Status | Condition | Detail |
|---|---|---|
| 404 | Job ID not found or expired | `"Job not found"` |
| 503 | Redis unavailable | `"Redis is unavailable"` |

---

## GET /

Serves the Distill web UI (`static/index.html`). This is a convenience
endpoint for browser-based testing and is not part of the programmatic API.

---

## Webhook Callbacks

When `callback_url` is provided to `POST /api/convert`, the worker POSTs the
full job result as JSON to that URL after conversion completes or fails.

### Callback Validation Rules

- Must use HTTPS (`http://` is rejected).
- Must not target `localhost` or `localhost.localdomain`.
- Must not target private, loopback, reserved, or link-local IP addresses.

### Delivery Behaviour

- Delivery is best-effort with exponential backoff retry (up to 3 attempts: 1s, 2s, 4s delays).
- A 2xx response from the callback endpoint is considered successful.
- If all retries fail, the job status is set to `callback_failed`.
- The `User-Agent` header is set to `Distill-Webhook/1.0`.

### Callback Payload

The POST body is the same JSON object that would appear in the `result` field
of a `GET /jobs/{job_id}` response when the job is complete, or an error object
when the job has failed.

---

## Queue Routing

Async jobs are routed to one of two Celery queues:

| Queue | Name | Purpose |
|---|---|---|
| Interactive | `distill.interactive` | Low-latency queue for small files where a user is waiting. |
| Batch | `distill.batch` | Throughput queue for large files and audio. |

### Automatic Routing

When `priority` is not specified:

- Audio files (by MIME type) are always routed to `distill.batch`.
- Files exceeding the async size threshold are routed to `distill.batch`.
- All other files are routed to `distill.interactive`.

### Manual Override

Set `priority=interactive` or `priority=batch` to override automatic routing.
