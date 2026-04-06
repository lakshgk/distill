"""
distill_app.server
~~~~~~~~~~~~~~~~~~
FastAPI server for Distill.

Routes:
    GET  /                  → serves static/index.html
    POST /api/convert       → convert uploaded file, return JSON (sync or async)
    GET  /jobs/{job_id}     → poll async job status
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
import webbrowser
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from threading import Timer
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from distill_app import settings
from distill_app.jobs import JobStore, JobStatus, JobStoreError
from distill_app.queues import route_job

_logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ── Async configuration ─────────────────────────────────────────────────────

_ASYNC_SIZE_THRESHOLD = settings.ASYNC_SIZE_THRESHOLD_MB * 1024 * 1024

ALWAYS_ASYNC_FORMATS = {
    "audio/mpeg", "audio/wav", "audio/mp4",
    "audio/flac", "audio/ogg", "scanned_pdf",
}

_REDACTED_FIELDS = {
    "llm_api_key", "hf_token", "vision_api_key",
    "access_token", "google_credentials",
    "openai_api_key", "anthropic_api_key",
}


def _redact(d: dict) -> dict:
    """Return a copy of *d* with sensitive keys replaced by '***'.

    Also redacts matching keys inside a nested ``extra`` dict.
    """
    out = {}
    for k, v in d.items():
        if k in _REDACTED_FIELDS:
            out[k] = "***"
        elif k == "extra" and isinstance(v, dict):
            out[k] = {ek: ("***" if ek in _REDACTED_FIELDS else ev) for ek, ev in v.items()}
        else:
            out[k] = v
    return out


# ── Module-level state ──────────────────────────────────────────────────────

job_store: Optional[JobStore] = None
_redis_healthy: bool = False


# ── Async detection ─────────────────────────────────────────────────────────

def should_run_async(file_size_bytes: int, format: str) -> bool:
    """Return True if this request should be processed asynchronously.

    Returns False when Redis is unhealthy so the API degrades to sync.
    Never raises.
    """
    try:
        if not _redis_healthy:
            return False
        if format in ALWAYS_ASYNC_FORMATS:
            return True
        if file_size_bytes > _ASYNC_SIZE_THRESHOLD:
            return True
        return False
    except Exception:
        return False


# ── Options serialisation ───────────────────────────────────────────────────

def serialise_options(options) -> dict:
    """Serialise a ParseOptions to a JSON-safe dict for Celery task args."""
    from distill.parsers.base import ParseOptions
    d = {}
    for f in dataclass_fields(ParseOptions):
        if f.name == "collector":
            continue  # not serialisable
        val = getattr(options, f.name, None)
        # LLMConfig is a dataclass — convert to dict for JSON serialisation
        if f.name == "llm" and val is not None:
            from dataclasses import asdict
            val = asdict(val)
        d[f.name] = val
    return d


def deserialise_options(d: dict):
    """Rebuild a ParseOptions from a dict."""
    from distill.parsers.base import ParseOptions
    known = {f.name for f in dataclass_fields(ParseOptions)}
    filtered = {k: v for k, v in d.items() if k in known and k != "collector"}
    # Rebuild LLMConfig from dict if present
    if "llm" in filtered and isinstance(filtered["llm"], dict):
        from distill.features.llm import LLMConfig
        filtered["llm"] = LLMConfig(**filtered["llm"])
    return ParseOptions(**filtered)


# ── App factory ─────────────────────────────────────────────────────────────

def build_app():
    global job_store, _redis_healthy

    app = FastAPI(title="Distill", version="0.1.0")

    # ── Lifecycle ────────────────────────────────────────────────────────────

    @app.on_event("startup")
    async def _startup():
        global job_store, _redis_healthy
        job_store = JobStore(redis_url=settings.REDIS_URL, ttl_seconds=settings.JOB_TTL_SECONDS)
        _redis_healthy = job_store.ping()
        asyncio.create_task(_redis_health_loop())

    async def _redis_health_loop():
        global _redis_healthy
        while True:
            await asyncio.sleep(30)
            try:
                _redis_healthy = job_store.ping() if job_store else False
            except Exception:
                _redis_healthy = False

    # ── Static files ─────────────────────────────────────────────────────────

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    if (STATIC_DIR).exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ── Convert endpoint ─────────────────────────────────────────────────────

    @app.post("/api/convert")
    async def convert(
        file:              UploadFile       = File(...),
        include_metadata:  bool             = Form(True),
        max_rows:          int              = Form(500),
        enable_ocr:        bool             = Form(False),
        extract_content:   bool             = Form(False),
        output_format:     str              = Form("markdown"),
        llm_merge_tables:  bool             = Form(False),
        llm_api_key:       str              = Form(""),
        llm_model:         str              = Form(""),
        extract:               bool             = Form(False),
        schema:                str              = Form(""),
        transcription_engine:  str              = Form("whisper"),
        whisper_model:         str              = Form("base"),
        hf_token:              str              = Form(""),
        topic_segmentation:    bool             = Form(False),
        callback_url:          Optional[str]    = Form(default=None),
        priority:              Optional[str]    = Form(default=None),
    ):
        import json as _json
        from distill import convert as _convert, ParseOptions
        from distill.parsers.base import DistillError

        VALID_OUTPUT_FORMATS = {"markdown", "json", "html", "chunks"}
        if output_format not in VALID_OUTPUT_FORMATS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid output_format {output_format!r}. "
                       f"Accepted: {', '.join(sorted(VALID_OUTPUT_FORMATS))}",
            )

        SUPPORTED = {".docx", ".doc", ".odt", ".xlsx", ".xls", ".xlsm", ".csv",
                     ".pptx", ".ppt", ".pdf", ".html", ".htm",
                     ".epub", ".wsdl", ".wsd", ".json", ".sql",
                     ".mp3", ".wav", ".m4a", ".flac", ".ogg"}

        AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}

        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SUPPORTED:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported format: '{suffix}'. "
                       f"Supported: {', '.join(sorted(SUPPORTED))}",
            )

        is_audio = suffix in AUDIO_EXTENSIONS

        # ── Priority validation ─────────────────────────────────────────────
        if priority is not None and priority not in ("interactive", "batch"):
            raise HTTPException(
                status_code=422,
                detail="priority must be 'interactive' or 'batch'",
            )

        # ── Callback URL validation ─────────────────────────────────────────
        if callback_url:
            from distill_app.webhooks import validate_callback_url
            try:
                validate_callback_url(callback_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

        # ── Audio-specific validations ───────────────────────────────────────
        if is_audio:
            if output_format in ("json", "html"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Audio input does not support output_format={output_format!r}. "
                           "Supported for audio: markdown, chunks.",
                )
            if transcription_engine not in ("whisper", "vosk"):
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown transcription_engine: {transcription_engine!r}. "
                           "Supported: whisper, vosk.",
                )
            if transcription_engine == "vosk":
                raise HTTPException(
                    status_code=422,
                    detail="Vosk transcription requires a model path. "
                           "Configure vosk_model_path in the server environment.",
                )

        # ── Topic segmentation validation (audio only) ───────────────────────
        # Note: for non-audio input, topic_segmentation is silently ignored
        # by the pipeline guard in convert()

        # ── Parse and validate schema ────────────────────────────────────────
        parsed_schema = None
        if extract:
            if not schema.strip():
                raise HTTPException(
                    status_code=422,
                    detail="extract=True requires a non-empty schema",
                )
            try:
                parsed_schema = _json.loads(schema)
            except _json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"schema is not valid JSON: {exc}",
                )
            if not isinstance(parsed_schema, dict):
                raise HTTPException(
                    status_code=422,
                    detail="schema must be a JSON object (dict), not "
                           f"{type(parsed_schema).__name__}",
                )
            if not parsed_schema:
                raise HTTPException(
                    status_code=422,
                    detail="extract=True requires a non-empty schema dict",
                )

        # ── Resolve LLM config with env var fallbacks ────────────────────────
        resolved_key   = llm_api_key.strip() or settings.LLM_API_KEY
        resolved_model = llm_model.strip()   or settings.LLM_MODEL

        if llm_merge_tables and not resolved_key:
            raise HTTPException(
                status_code=422,
                detail="llm_merge_tables requires llm_api_key and llm_model to be set",
            )

        if extract and not resolved_key:
            raise HTTPException(
                status_code=422,
                detail="extract=True requires llm_api_key and llm_model to be set",
            )

        if topic_segmentation and is_audio and not resolved_key:
            raise HTTPException(
                status_code=422,
                detail="topic_segmentation requires an LLM API key. Provide "
                       "llm_api_key as a form field or set DISTILL_LLM_API_KEY "
                       "in your environment.",
            )

        llm_config = None
        if resolved_key and resolved_model:
            from distill.features.llm import LLMConfig
            llm_config = LLMConfig(api_key=resolved_key, model=resolved_model)

        # Resolve audio options
        resolved_hf_token = hf_token.strip() or settings.HF_TOKEN or None

        # Read uploaded file
        file_bytes = await file.read()
        file_size  = len(file_bytes)

        # Audio is always async — never synchronous
        if is_audio:
            if not _redis_healthy:
                raise HTTPException(
                    status_code=503,
                    detail="Audio conversion requires async processing but the "
                           "job queue is unavailable. Please try again later.",
                )
            queue_name = route_job(file_size, file.content_type or "", priority)
            return await _handle_async(
                file_bytes, suffix, include_metadata, max_rows, enable_ocr,
                extract_content, output_format, llm_merge_tables, llm_config,
                transcription_engine=transcription_engine,
                whisper_model=whisper_model,
                hf_token=resolved_hf_token,
                topic_segmentation=topic_segmentation,
                callback_url=callback_url,
                queue_name=queue_name,
            )

        # Check if this should be async
        use_async = should_run_async(file_size, file.content_type or "")

        if use_async:
            queue_name = route_job(file_size, file.content_type or "", priority)
            return await _handle_async(file_bytes, suffix, include_metadata,
                                       max_rows, enable_ocr, extract_content,
                                       output_format, llm_merge_tables, llm_config,
                                       topic_segmentation=topic_segmentation,
                                       callback_url=callback_url,
                                       queue_name=queue_name)

        # ── Sync path ───────────────────────────────────────────────────────
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        try:
            options = ParseOptions(
                max_table_rows=max_rows,
                extra={"enable_ocr": enable_ocr, "extract_content": extract_content},
                output_format=output_format,
                llm_merge_tables=llm_merge_tables,
                llm=llm_config,
                extract=extract,
                schema=parsed_schema,
                topic_segmentation=topic_segmentation,
            )
            result = _convert(
                tmp_path,
                include_metadata=include_metadata,
                options=options,
            )
        except DistillError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
        finally:
            tmp_path.unlink(missing_ok=True)

        # Build quality breakdown
        qs = result.quality_details
        if result.quality_score is None:
            quality = {"overall": None, "error": qs.error if qs else "Unknown error", "components": None}
        else:
            quality = {"overall": round(result.quality_score, 3)}
            if qs is not None:
                quality.update({
                    "headings":   round(qs.heading_preservation, 3),
                    "tables":     round(qs.table_preservation, 3),
                    "lists":      round(qs.list_preservation, 3),
                    "efficiency": round(qs.token_reduction_ratio, 3),
                })

        # Build stats
        meta  = result.metadata
        stats = {
            "words":  getattr(meta, "word_count",  None),
            "pages":  getattr(meta, "page_count",  None),
            "slides": getattr(meta, "slide_count", None),
            "sheets": getattr(meta, "sheet_count", None),
            "format": (getattr(meta, "source_format", None) or "").upper() or None,
        }

        try:
            warnings_out = result.structured_warnings
        except Exception:
            warnings_out = []

        # Build response envelope
        if output_format == "chunks":
            if result.chunks is None:
                raise HTTPException(status_code=500, detail="Chunks renderer returned no output.")
            try:
                chunks_dicts = [c.to_dict() for c in result.chunks]
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Chunks serialisation failed: {exc}")
            envelope = {
                "chunks":      chunks_dicts,
                "chunk_count": len(chunks_dicts),
                "quality":     quality,
                "stats":       stats,
                "warnings":    warnings_out,
            }
        elif output_format == "json":
            if result.document_json is None:
                raise HTTPException(status_code=500, detail="JSON renderer returned no output.")
            envelope = {
                "document": result.document_json,
                "quality":  quality,
                "stats":    stats,
                "warnings": warnings_out,
            }
        elif output_format == "html":
            if result.html is None:
                raise HTTPException(status_code=500, detail="HTML renderer returned no output.")
            envelope = {
                "html":     result.html,
                "quality":  quality,
                "stats":    stats,
                "warnings": warnings_out,
            }
        else:
            envelope = {
                "markdown": result.markdown,
                "quality":  quality,
                "stats":    stats,
                "warnings": warnings_out,
            }

        # Include extracted data only when extraction was requested and succeeded
        if extract and result.extracted is not None:
            envelope["extracted"] = result.extracted

        return JSONResponse(envelope)

    # ── Async handler ────────────────────────────────────────────────────────

    async def _handle_async(
        file_bytes: bytes,
        suffix: str,
        include_metadata: bool,
        max_rows: int,
        enable_ocr: bool,
        extract_content: bool,
        output_format: str,
        llm_merge_tables: bool = False,
        llm_config=None,
        transcription_engine: str = "whisper",
        whisper_model: str = "medium",
        hf_token: Optional[str] = None,
        topic_segmentation: bool = False,
        callback_url: Optional[str] = None,
        queue_name: Optional[str] = None,
    ) -> JSONResponse:
        from distill.parsers.base import ParseOptions
        from distill_app.worker import convert_document

        job_id = str(uuid.uuid4())

        # Write to a persistent temp directory (worker deletes after conversion)
        tmp_dir = tempfile.mkdtemp(prefix="distill_async_")
        tmp_path = Path(tmp_dir) / f"upload{suffix}"
        tmp_path.write_bytes(file_bytes)

        options = ParseOptions(
            max_table_rows=max_rows,
            extra={"enable_ocr": enable_ocr, "extract_content": extract_content},
            output_format=output_format,
            llm_merge_tables=llm_merge_tables,
            llm=llm_config,
            transcription_engine=transcription_engine,
            whisper_model=whisper_model,
            hf_token=hf_token,
            topic_segmentation=topic_segmentation,
        )
        options_dict = serialise_options(options)

        try:
            job_store.set_queued(job_id)
            convert_document.apply_async(
                args=[job_id, str(tmp_path), options_dict],
                kwargs={"callback_url": callback_url, "queue_name": queue_name},
                queue=queue_name,
            )
        except Exception as exc:
            # Redis became unhealthy between check and enqueue — fall back to sync
            _logger.warning("Async enqueue failed, falling back to sync: %s", exc)
            tmp_path.unlink(missing_ok=True)
            try:
                Path(tmp_dir).rmdir()
            except Exception:
                pass

            from distill import convert as _convert
            from distill.parsers.base import DistillError

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_bytes)
                sync_path = Path(tmp.name)

            try:
                result = _convert(sync_path, include_metadata=include_metadata, options=options)
            except DistillError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")
            finally:
                sync_path.unlink(missing_ok=True)

            # Reuse sync response building (inline for clarity)
            qs = result.quality_details
            if result.quality_score is None:
                quality = {"overall": None, "error": qs.error if qs else "Unknown error", "components": None}
            else:
                quality = {"overall": round(result.quality_score, 3)}
                if qs is not None:
                    quality.update({
                        "headings": round(qs.heading_preservation, 3),
                        "tables":   round(qs.table_preservation, 3),
                        "lists":    round(qs.list_preservation, 3),
                        "efficiency": round(qs.token_reduction_ratio, 3),
                    })

            meta = result.metadata
            stats = {
                "words":  getattr(meta, "word_count", None),
                "pages":  getattr(meta, "page_count", None),
                "slides": getattr(meta, "slide_count", None),
                "sheets": getattr(meta, "sheet_count", None),
                "format": (getattr(meta, "source_format", None) or "").upper() or None,
            }
            try:
                warnings_out = result.structured_warnings
            except Exception:
                warnings_out = []

            return JSONResponse(
                {"markdown": result.markdown, "quality": quality, "stats": stats, "warnings": warnings_out},
                headers={"X-Distill-Async": "degraded"},
            )

        return JSONResponse(
            {
                "job_id":   job_id,
                "status":   "queued",
                "poll_url": f"/jobs/{job_id}",
                "queue":    queue_name,
            },
            status_code=202,
        )

    # ── Job polling endpoint ─────────────────────────────────────────────────

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        if not _redis_healthy:
            raise HTTPException(status_code=503, detail="Redis is unavailable")

        try:
            job = job_store.get(job_id)
        except JobStoreError:
            raise HTTPException(status_code=503, detail="Redis is unavailable")

        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        response: dict = {"job_id": job.job_id, "status": job.status.value, "queue": job.queue}
        if job.status == JobStatus.COMPLETE and job.result is not None:
            response["result"] = job.result
        if job.status == JobStatus.FAILED and job.error is not None:
            response["error"] = job.error

        return JSONResponse(response)

    # ── SSE progress streaming ──────────────────────────────────────────────

    _TERMINAL_STATUSES = {
        JobStatus.COMPLETE.value,
        JobStatus.FAILED.value,
        JobStatus.CALLBACK_FAILED.value,
    }

    @app.get("/jobs/{job_id}/stream")
    async def stream_job(job_id: str):
        """Server-Sent Events stream for real-time job progress."""
        import json as _json
        import time as _time

        from distill_app.progress import progress_channel

        # 1. Job existence check
        if not _redis_healthy:
            raise HTTPException(status_code=503, detail="Redis is unavailable")

        try:
            job = job_store.get(job_id)
        except JobStoreError:
            raise HTTPException(status_code=503, detail="Redis is unavailable")

        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        # 2. Already terminal — send final event and close
        if job.status.value in _TERMINAL_STATUSES:
            final = {
                "job_id": job.job_id,
                "status": job.status.value,
                "queue": job.queue,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if job.status == JobStatus.COMPLETE and job.result is not None:
                final["pct"] = 100

            async def _final_generator():
                yield f"data: {_json.dumps(final)}\n\n"

            return StreamingResponse(
                _final_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # 3. Subscribe and stream
        keepalive_seconds = getattr(settings, "SSE_KEEPALIVE_SECONDS", 15)
        max_duration_seconds = getattr(settings, "SSE_MAX_DURATION_SECONDS", 3600)

        async def _event_generator():
            import redis.asyncio as aioredis

            start_time = _time.monotonic()

            try:
                r = aioredis.from_url(settings.REDIS_URL)
                pubsub = r.pubsub()
                await pubsub.subscribe(progress_channel(job_id))
            except Exception as exc:
                _logger.warning("SSE Redis connect failed for job %s: %s", job_id, exc)
                error_event = {
                    "status": "error",
                    "message": "Progress stream unavailable",
                }
                yield f"data: {_json.dumps(error_event)}\n\n"
                return

            try:
                while True:
                    # Check max duration
                    elapsed = _time.monotonic() - start_time
                    if elapsed >= max_duration_seconds:
                        timeout_event = {
                            "status": "timeout",
                            "message": "Stream duration limit reached",
                        }
                        yield f"data: {_json.dumps(timeout_event)}\n\n"
                        return

                    try:
                        msg = await asyncio.wait_for(
                            pubsub.get_message(
                                ignore_subscribe_messages=True,
                                timeout=keepalive_seconds,
                            ),
                            timeout=keepalive_seconds + 1,
                        )
                    except asyncio.TimeoutError:
                        msg = None

                    if msg is None:
                        # Heartbeat
                        yield ": heartbeat\n\n"
                        continue

                    if msg["type"] != "message":
                        continue

                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")

                    yield f"data: {data}\n\n"

                    # Check if terminal
                    try:
                        parsed = _json.loads(data)
                        if parsed.get("status") in ("completed", "failed"):
                            return
                    except Exception:
                        pass

            except Exception as exc:
                _logger.warning("SSE stream error for job %s: %s", job_id, exc)
                error_event = {
                    "status": "error",
                    "message": "Progress stream unavailable",
                }
                yield f"data: {_json.dumps(error_event)}\n\n"
            finally:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                    await r.close()
                except Exception:
                    pass

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def launch(
    host:      str  = "127.0.0.1",
    port:      int  = 7860,
    inbrowser: bool = True,
):
    import uvicorn

    app = build_app()

    if inbrowser:
        url = f"http://{host}:{port}"
        Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=host, port=port, log_level="warning")
