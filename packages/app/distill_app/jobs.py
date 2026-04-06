"""
distill_app.jobs
~~~~~~~~~~~~~~~~
Job storage service backed by Redis.

Tracks async conversion jobs through their lifecycle:
    QUEUED → PROCESSING → COMPLETE | FAILED

Keys use the pattern ``distill:job:{job_id}`` with a configurable TTL.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

_logger = logging.getLogger(__name__)

_KEY_PREFIX = "distill:job:"


# ── Exceptions ──────────────────────────────────────────────────────────────

class JobStoreError(Exception):
    """Raised when a JobStore operation fails."""


# ── Enums / dataclasses ────────────────────────────────────────────────────

class JobStatus(str, Enum):
    QUEUED          = "queued"
    PROCESSING      = "processing"
    COMPLETE        = "complete"
    FAILED          = "failed"
    CALLBACK_FAILED = "callback_failed"


@dataclass
class JobResult:
    job_id:     str
    status:     JobStatus
    result:     Optional[dict] = None
    error:      Optional[str]  = None
    queue:      Optional[str]  = None
    created_at: float          = field(default_factory=time.time)
    updated_at: float          = field(default_factory=time.time)


# ── Store ───────────────────────────────────────────────────────────────────

class JobStore:
    """Redis-backed store for async job state."""

    def __init__(self, redis_url: str, ttl_seconds: int = 3600) -> None:
        self._redis_url = redis_url
        self._ttl = ttl_seconds
        self._redis = None  # lazy connection

    def _client(self):
        """Return the Redis client, connecting lazily on first use."""
        if self._redis is None:
            import redis
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    # ── Write helpers ────────────────────────────────────────────────────────

    def set_queued(self, job_id: str) -> None:
        now = time.time()
        job = JobResult(job_id=job_id, status=JobStatus.QUEUED, created_at=now, updated_at=now)
        self._write(job)

    def set_processing(self, job_id: str, queue: str | None = None) -> None:
        job = self._read_or_new(job_id)
        job.status = JobStatus.PROCESSING
        if queue is not None:
            job.queue = queue
        job.updated_at = time.time()
        self._write(job)

    def set_complete(self, job_id: str, result: dict) -> None:
        job = self._read_or_new(job_id)
        job.status = JobStatus.COMPLETE
        job.result = result
        job.updated_at = time.time()
        self._write(job)

    def set_failed(self, job_id: str, error: str) -> None:
        job = self._read_or_new(job_id)
        job.status = JobStatus.FAILED
        job.error = error
        job.updated_at = time.time()
        self._write(job)

    def set_callback_failed(self, job_id: str) -> None:
        job = self._read_or_new(job_id)
        job.status = JobStatus.CALLBACK_FAILED
        job.updated_at = time.time()
        self._write(job)

    # ── Read ─────────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[JobResult]:
        """Return the JobResult for *job_id*, or None if not found."""
        try:
            raw = self._client().get(f"{_KEY_PREFIX}{job_id}")
        except Exception as exc:
            _logger.error("Redis GET failed for job %s: %s", job_id, exc)
            raise JobStoreError(f"Redis GET failed: {exc}") from exc

        if raw is None:
            return None

        data = json.loads(raw)
        data["status"] = JobStatus(data["status"])
        return JobResult(**data)

    # ── Health ───────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Return True if Redis responds to PING. Never raises."""
        try:
            return bool(self._client().ping())
        except Exception:
            return False

    # ── Internal ─────────────────────────────────────────────────────────────

    def _write(self, job: JobResult) -> None:
        key = f"{_KEY_PREFIX}{job.job_id}"
        data = asdict(job)
        data["status"] = job.status.value
        try:
            self._client().setex(key, self._ttl, json.dumps(data))
        except Exception as exc:
            _logger.error("Redis SETEX failed for job %s: %s", job.job_id, exc)
            raise JobStoreError(f"Redis SETEX failed: {exc}") from exc

    def _read_or_new(self, job_id: str) -> JobResult:
        """Read existing job or create a fresh one (preserves created_at)."""
        existing = self.get(job_id)
        if existing is not None:
            return existing
        return JobResult(job_id=job_id, status=JobStatus.QUEUED)
