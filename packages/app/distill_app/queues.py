"""
distill_app.queues
~~~~~~~~~~~~~~~~~~
Queue name constants and job routing logic.

Two named Celery queues separate interactive (human-waiting) traffic from
batch (background) traffic so that batch jobs can never starve interactive
requests.
"""

from __future__ import annotations

import logging

from distill_app import settings

_logger = logging.getLogger(__name__)

# ── Queue constants ────────────────────────────────────────────────────────

QUEUE_INTERACTIVE = "distill.interactive"
QUEUE_BATCH = "distill.batch"
QUEUE_DEFAULT = QUEUE_INTERACTIVE  # fallback if routing fails

# ── Audio MIME types ───────────────────────────────────────────────────────

AUDIO_MIME_TYPES: frozenset[str] = frozenset({
    "audio/mpeg",
    "audio/wav",
    "audio/mp4",
    "audio/flac",
    "audio/ogg",
    "video/mp4",
})

# ── Routing ────────────────────────────────────────────────────────────────


def route_job(
    file_size_bytes: int,
    mime_type: str,
    priority: str | None,
) -> str:
    """Determine which Celery queue a job should be routed to.

    Parameters
    ----------
    file_size_bytes:
        Size of the uploaded file in bytes.
    mime_type:
        MIME type reported by the upload.
    priority:
        Optional caller-supplied override: ``"interactive"`` or ``"batch"``.
        Any other non-None value raises ``ValueError``.

    Returns
    -------
    str
        One of :data:`QUEUE_INTERACTIVE` or :data:`QUEUE_BATCH`.
    """
    # ── Manual override ─────────────────────────────────────────────────
    if priority is not None:
        if priority == "interactive":
            return QUEUE_INTERACTIVE
        if priority == "batch":
            return QUEUE_BATCH
        raise ValueError("priority must be 'interactive' or 'batch'")

    # ── Automatic routing ───────────────────────────────────────────────
    try:
        # Audio always goes to batch regardless of size
        if mime_type in AUDIO_MIME_TYPES:
            return QUEUE_BATCH

        threshold_bytes = settings.ASYNC_SIZE_THRESHOLD_MB * 1024 * 1024
        if file_size_bytes > threshold_bytes:
            return QUEUE_BATCH

        return QUEUE_INTERACTIVE

    except Exception as exc:
        _logger.warning("Routing error, falling back to default queue: %s", exc)
        return QUEUE_DEFAULT


# TODO: Scanned PDF demotion — when the PDF parser detects a scanned page
# mid-conversion, the worker should publish a ``queue_demoted`` progress
# event.  This requires detecting scanned content at parse time and is
# deferred to a future spec.
