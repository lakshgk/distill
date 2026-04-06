"""
distill_app.progress
~~~~~~~~~~~~~~~~~~~~
Progress event publishing for async jobs.

Workers emit progress events to a Redis pub/sub channel. The SSE endpoint
subscribes to that channel and streams events to clients in real time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_logger = logging.getLogger(__name__)


# ── Channel naming ─────────────────────────────────────────────────────────


def progress_channel(job_id: str) -> str:
    """Return the Redis pub/sub channel name for a job's progress events."""
    return f"distill.progress.{job_id}"


# ── Progress event ─────────────────────────────────────────────────────────


@dataclass
class ProgressEvent:
    """A single progress event emitted by the worker pipeline."""

    job_id: str
    status: str
    queue: str
    ts: str = field(default="")
    stage: Optional[str] = None
    pct: Optional[int] = None
    message: Optional[str] = None

    def __post_init__(self):
        if not self.ts:
            self.ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_dict(self) -> dict:
        """Serialise to a dict, omitting None optional fields.

        ``job_id``, ``status``, ``queue``, and ``ts`` are always included.
        """
        d: dict = {
            "job_id": self.job_id,
            "status": self.status,
            "queue": self.queue,
            "ts": self.ts,
        }
        if self.stage is not None:
            d["stage"] = self.stage
        if self.pct is not None:
            d["pct"] = self.pct
        if self.message is not None:
            d["message"] = self.message
        return d


# ── Publisher ──────────────────────────────────────────────────────────────


class ProgressPublisher:
    """Publishes progress events to a Redis pub/sub channel.

    Safe to use even when Redis is unavailable — all errors are swallowed
    and logged at WARNING level.  After the first connection failure the
    publisher stops attempting to reconnect for the lifetime of the instance
    (avoids log spam on long-running jobs).
    """

    def __init__(self, redis_url: str, job_id: str, queue: str) -> None:
        self._redis_url = redis_url
        self._job_id = job_id
        self._queue = queue
        self._channel = progress_channel(job_id)
        self._redis = None  # lazy
        self._available: bool = True  # optimistic until first failure

    def emit(
        self,
        status: str,
        stage: str | None = None,
        pct: int | None = None,
        message: str | None = None,
    ) -> None:
        """Publish a progress event.  Never raises."""
        if not self._available:
            return

        try:
            event = ProgressEvent(
                job_id=self._job_id,
                status=status,
                queue=self._queue,
                stage=stage,
                pct=pct,
                message=message,
            )
            payload = json.dumps(event.to_dict())
            self._get_redis().publish(self._channel, payload)
        except Exception as exc:
            _logger.warning(
                "Progress publish failed for job %s (disabling): %s",
                self._job_id, exc,
            )
            self._available = False

    def close(self) -> None:
        """Close the Redis connection if open.  Never raises."""
        try:
            if self._redis is not None:
                self._redis.close()
        except Exception:
            pass
        self._redis = None

    # ── Internal ────────────────────────────────────────────────────────

    def _get_redis(self):
        """Return the Redis client, connecting lazily on first use."""
        if self._redis is None:
            import redis
            self._redis = redis.from_url(self._redis_url)
        return self._redis
