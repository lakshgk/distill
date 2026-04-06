"""
distill_app.worker
~~~~~~~~~~~~~~~~~~
Celery worker for async document conversion.

Start with:
    celery -A distill_app.worker worker --loglevel=info -Q conversions
"""

from __future__ import annotations

import logging
from pathlib import Path

from celery import Celery

from distill_app import settings
from distill_app.jobs import JobStore
from distill_app.progress import ProgressPublisher
from distill_app.queues import QUEUE_DEFAULT

_logger = logging.getLogger(__name__)

# ── Celery app ──────────────────────────────────────────────────────────────

celery_app = Celery(
    "distill",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_default_queue="conversions",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_always_eager=False,
    task_track_started=True,
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=False,
)

# ── Module-level job store ──────────────────────────────────────────────────

job_store = JobStore(
    redis_url=settings.REDIS_URL,
    ttl_seconds=settings.JOB_TTL_SECONDS,
)

# ── Task ────────────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=5, trail=False)
def convert_document(
    self, job_id: str, file_path: str, options_dict: dict,
    callback_url: str | None = None,
    queue_name: str | None = None,
) -> dict:
    """
    Convert a document asynchronously.

    Steps:
        1. Mark job as PROCESSING in Redis.
        2. Deserialise options and run conversion.
        3. Serialise result to the API response envelope.
        4. Mark job as COMPLETE and delete the temp file.
        5. If callback_url is set, deliver the result via webhook.

    Deterministic failures (ParseError, conversion errors) are not retried.
    Transient failures (Redis, OS errors) trigger retry with backoff.
    """
    from distill import convert as _convert, ParseOptions
    from distill.parsers.base import DistillError

    resolved_queue = queue_name or QUEUE_DEFAULT
    tmp_path = Path(file_path)

    publisher = ProgressPublisher(
        redis_url=settings.REDIS_URL,
        job_id=job_id,
        queue=resolved_queue,
    )

    try:
        publisher.emit("processing", stage="routing", pct=2)

        try:
            job_store.set_processing(job_id, queue=resolved_queue)
        except Exception as exc:
            _logger.warning("Failed to set PROCESSING for %s: %s", job_id, exc)
            raise self.retry(exc=exc)

        result_dict = None
        job_failed = False

        try:
            options = _deserialise_options(options_dict)

            # Pass publisher to parsers via options.extra
            if options.extra is None:
                options.extra = {}
            options.extra["progress_publisher"] = publisher

            publisher.emit("processing", stage="parsing", pct=10)
            result = _convert(tmp_path, options=options, _async_context=True)

            publisher.emit("processing", stage="quality_check", pct=85)
            result_dict = _serialise_result(result, options)

            publisher.emit("processing", stage="rendering", pct=90)
            job_store.set_complete(job_id, result_dict)
            tmp_path.unlink(missing_ok=True)

        except DistillError as exc:
            _logger.error("Conversion failed for job %s: %s", job_id, exc)
            job_store.set_failed(job_id, str(exc))
            result_dict = {"job_id": job_id, "status": "failed", "error": str(exc)}
            job_failed = True
            tmp_path.unlink(missing_ok=True)
            publisher.emit("failed", message=str(exc))

        except OSError as exc:
            _logger.warning("Transient OS error for job %s: %s", job_id, exc)
            raise self.retry(exc=exc)

        except Exception as exc:
            if _is_transient(exc):
                _logger.warning("Transient error for job %s, retrying: %s", job_id, exc)
                raise self.retry(exc=exc)
            _logger.error("Unexpected error for job %s: %s", job_id, exc)
            job_store.set_failed(job_id, str(exc))
            result_dict = {"job_id": job_id, "status": "failed", "error": str(exc)}
            job_failed = True
            tmp_path.unlink(missing_ok=True)
            publisher.emit("failed", message=str(exc))

        # ── Webhook delivery ─────────────────────────────────────────────
        if callback_url and result_dict is not None:
            try:
                from distill_app.webhooks import WebhookDelivery, _redact_url

                publisher.emit("processing", stage="delivering_webhook", pct=95)

                timeout = getattr(settings, "WEBHOOK_TIMEOUT_SECONDS", 10)
                delivery = WebhookDelivery(timeout_seconds=timeout)

                payload = {"job_id": job_id, "status": "failed" if job_failed else "complete"}
                if job_failed:
                    payload["error"] = result_dict.get("error", "Unknown error")
                else:
                    payload["result"] = result_dict

                ok = delivery.deliver_with_retry(callback_url, payload, max_retries=3)

                if ok:
                    _logger.info("Webhook delivered for job %s to %s", job_id, _redact_url(callback_url))
                else:
                    _logger.error("Webhook delivery failed for job %s to %s", job_id, _redact_url(callback_url))
                    job_store.set_callback_failed(job_id)
            except Exception as exc:
                _logger.error("Webhook delivery error for job %s: %s", job_id, exc)

        if not job_failed:
            publisher.emit("completed", pct=100)

        if job_failed:
            raise DistillError(result_dict.get("error", "Conversion failed"))

        return result_dict

    finally:
        publisher.close()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _is_transient(exc: Exception) -> bool:
    """Return True if the exception is likely transient (retry-worthy)."""
    import redis
    return isinstance(exc, (redis.ConnectionError, redis.TimeoutError, ConnectionError))


def _deserialise_options(d: dict) -> "ParseOptions":
    """Rebuild a ParseOptions from a dict."""
    from distill.parsers.base import ParseOptions
    known_fields = {f.name for f in ParseOptions.__dataclass_fields__.values()}
    filtered = {k: v for k, v in d.items() if k in known_fields}
    return ParseOptions(**filtered)


def _serialise_result(result, options) -> dict:
    """Build the same JSON response envelope as the sync API endpoint."""
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

    envelope = {
        "quality":  quality,
        "stats":    stats,
        "warnings": warnings_out,
    }

    fmt = options.output_format if options else "markdown"

    if fmt == "chunks" and result.chunks is not None:
        try:
            envelope["chunks"] = [c.to_dict() for c in result.chunks]
            envelope["chunk_count"] = len(envelope["chunks"])
        except Exception:
            envelope["chunks"] = []
            envelope["chunk_count"] = 0
    elif fmt == "json" and result.document_json is not None:
        envelope["document"] = result.document_json
    elif fmt == "html" and result.html is not None:
        envelope["html"] = result.html
    else:
        envelope["markdown"] = result.markdown

    return envelope
