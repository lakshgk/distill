"""
Tests for Distill async infrastructure: JobStore, should_run_async,
async API routing, and the convert_document Celery task.

Uses fakeredis for Redis-backed tests; no live Redis required.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from fastapi.testclient import TestClient

from distill_app.jobs import JobResult, JobStatus, JobStore, JobStoreError


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_fakeredis_store(ttl: int = 3600) -> JobStore:
    """Build a JobStore backed by fakeredis (no real Redis needed)."""
    store = JobStore(redis_url="redis://fake:6379/0", ttl_seconds=ttl)
    store._redis = fakeredis.FakeRedis(decode_responses=True)
    return store


def _mock_convert_result(
    markdown="# Hello\n\nWorld.",
    quality=0.90,
    warnings=None,
):
    from distill.quality import QualityScore

    qs = QualityScore(
        overall=quality,
        heading_preservation=1.0,
        table_preservation=1.0,
        list_preservation=1.0,
        token_reduction_ratio=0.80,
    )
    r = MagicMock()
    r.markdown = markdown
    r.quality_score = quality
    r.quality_details = qs
    r.warnings = warnings or []
    r.structured_warnings = warnings or []
    m = MagicMock()
    m.word_count = 120
    m.page_count = 3
    m.slide_count = None
    m.sheet_count = None
    m.source_format = "docx"
    r.metadata = m
    r.chunks = None
    r.document_json = None
    r.html = None
    return r


# ── Test 1: JobStore.ping() returns False for unreachable Redis ──────────────

class TestJobStorePing:
    def test_ping_returns_false_for_unreachable(self):
        store = JobStore(redis_url="redis://localhost:19999/0")
        assert store.ping() is False


# ── Tests 2-4: JobStore state transitions with fakeredis ─────────────────────

class TestJobStoreTransitions:
    def test_queued_to_processing_to_complete(self):
        store = _make_fakeredis_store()

        store.set_queued("job-1")
        job = store.get("job-1")
        assert job is not None
        assert job.status == JobStatus.QUEUED

        store.set_processing("job-1")
        job = store.get("job-1")
        assert job.status == JobStatus.PROCESSING

        store.set_complete("job-1", {"markdown": "# Done"})
        job = store.get("job-1")
        assert job.status == JobStatus.COMPLETE
        assert job.result == {"markdown": "# Done"}

    def test_set_failed_stores_error(self):
        store = _make_fakeredis_store()
        store.set_queued("job-2")
        store.set_failed("job-2", "Parse error: corrupt file")
        job = store.get("job-2")
        assert job.status == JobStatus.FAILED
        assert job.error == "Parse error: corrupt file"

    def test_ttl_applied_on_every_write(self):
        store = _make_fakeredis_store(ttl=600)
        store.set_queued("job-3")
        ttl = store._redis.ttl("distill:job:job-3")
        assert ttl > 0
        assert ttl <= 600


# ── Tests 5-8: should_run_async ──────────────────────────────────────────────

class TestShouldRunAsync:
    def test_small_file_non_async_format_returns_false(self):
        import distill_app.server as srv
        original = srv._redis_healthy
        try:
            srv._redis_healthy = True
            assert srv.should_run_async(1 * 1024 * 1024, "application/pdf") is False
        finally:
            srv._redis_healthy = original

    def test_large_file_returns_true(self):
        import distill_app.server as srv
        original = srv._redis_healthy
        try:
            srv._redis_healthy = True
            # 20 MB > default 10 MB threshold
            assert srv.should_run_async(20 * 1024 * 1024, "application/pdf") is True
        finally:
            srv._redis_healthy = original

    def test_returns_false_when_redis_unhealthy(self):
        import distill_app.server as srv
        original = srv._redis_healthy
        try:
            srv._redis_healthy = False
            assert srv.should_run_async(100 * 1024 * 1024, "application/pdf") is False
        finally:
            srv._redis_healthy = original

    def test_audio_format_always_async(self):
        import distill_app.server as srv
        original = srv._redis_healthy
        try:
            srv._redis_healthy = True
            assert srv.should_run_async(100, "audio/mpeg") is True
        finally:
            srv._redis_healthy = original


# ── Tests 9-10: POST /api/convert sync vs async ─────────────────────────────

class TestConvertEndpoint:
    @pytest.fixture()
    def client(self):
        from distill_app.server import build_app
        return TestClient(build_app())

    def test_small_docx_returns_sync_markdown(self, client):
        with patch("distill.convert", return_value=_mock_convert_result()):
            files = {"file": ("report.docx", io.BytesIO(b"fake"), "application/octet-stream")}
            data = {"include_metadata": "true", "max_rows": "500",
                    "enable_ocr": "false", "extract_content": "false"}
            resp = client.post("/api/convert", data=data, files=files)
        assert resp.status_code == 200
        body = resp.json()
        assert "markdown" in body
        assert "job_id" not in body

    def test_async_returns_202_with_job_id(self, client):
        with patch("distill.convert", return_value=_mock_convert_result()), \
             patch("distill_app.server.should_run_async", return_value=True), \
             patch("distill_app.server.job_store") as mock_store, \
             patch("distill_app.worker.convert_document") as mock_task:
            mock_store.set_queued = MagicMock()
            mock_task.delay = MagicMock()

            files = {"file": ("report.docx", io.BytesIO(b"fake"), "application/octet-stream")}
            data = {"include_metadata": "true", "max_rows": "500",
                    "enable_ocr": "false", "extract_content": "false"}
            resp = client.post("/api/convert", data=data, files=files)

        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert "poll_url" in body
        assert body["status"] == "queued"


# ── Tests 11-12: GET /jobs/{id} ──────────────────────────────────────────────

class TestGetJobEndpoint:
    @pytest.fixture()
    def client(self):
        from distill_app.server import build_app
        return TestClient(build_app())

    def test_unknown_job_returns_404(self, client):
        import distill_app.server as srv
        original = srv._redis_healthy
        original_store = srv.job_store
        try:
            srv._redis_healthy = True
            srv.job_store = _make_fakeredis_store()
            resp = client.get("/jobs/nonexistent-id")
            assert resp.status_code == 404
        finally:
            srv._redis_healthy = original
            srv.job_store = original_store

    def test_returns_503_when_redis_unhealthy(self, client):
        import distill_app.server as srv
        original = srv._redis_healthy
        try:
            srv._redis_healthy = False
            resp = client.get("/jobs/some-id")
            assert resp.status_code == 503
        finally:
            srv._redis_healthy = original


# ── Tests 13-15: convert_document task ───────────────────────────────────────

class TestConvertDocumentTask:
    def test_sets_complete_on_success(self):
        store = _make_fakeredis_store()
        store.set_queued("task-1")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()

        mock_result = _mock_convert_result()

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", return_value=mock_result):
            from distill_app.worker import convert_document
            convert_document(
                "task-1", tmp.name, {"max_table_rows": 500, "output_format": "markdown", "extra": {}}
            )

        job = store.get("task-1")
        assert job.status == JobStatus.COMPLETE
        assert job.result is not None

    def test_sets_failed_on_parse_error(self):
        store = _make_fakeredis_store()
        store.set_queued("task-2")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()

        from distill.parsers.base import DistillError, ParseError

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", side_effect=ParseError("bad file")):
            from distill_app.worker import convert_document
            with pytest.raises(DistillError):
                convert_document(
                    "task-2", tmp.name, {"max_table_rows": 500, "output_format": "markdown", "extra": {}}
                )

        job = store.get("task-2")
        assert job.status == JobStatus.FAILED
        assert "bad file" in job.error

    def test_deletes_temp_file_on_success(self):
        store = _make_fakeredis_store()
        store.set_queued("task-3")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()
        tmp_path = Path(tmp.name)

        mock_result = _mock_convert_result()

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", return_value=mock_result):
            from distill_app.worker import convert_document
            convert_document(
                "task-3", str(tmp_path), {"max_table_rows": 500, "output_format": "markdown", "extra": {}}
            )

        assert not tmp_path.exists(), "Temp file should be deleted after successful conversion"

    def test_does_not_delete_temp_file_on_transient_retry(self):
        store = _make_fakeredis_store()
        store.set_queued("task-4")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()
        tmp_path = Path(tmp.name)

        import redis as redis_lib

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert",
                   side_effect=redis_lib.ConnectionError("connection lost")):
            from distill_app.worker import convert_document
            # The task will try to retry, which raises Retry in test context
            from celery.exceptions import Retry
            with pytest.raises((Retry, redis_lib.ConnectionError)):
                convert_document(
                    "task-4", str(tmp_path),
                    {"max_table_rows": 500, "output_format": "markdown", "extra": {}}
                )

        assert tmp_path.exists(), "Temp file must NOT be deleted when retrying"
        # Cleanup
        tmp_path.unlink(missing_ok=True)
