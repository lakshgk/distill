"""
Tests for priority queue routing logic, API endpoint integration,
and worker queue_name handling.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import fakeredis
import pytest
from fastapi.testclient import TestClient

from distill_app.queues import (
    AUDIO_MIME_TYPES,
    QUEUE_BATCH,
    QUEUE_DEFAULT,
    QUEUE_INTERACTIVE,
    route_job,
)


# ── Routing logic (Tests 1-8) ─────────────────────────────────────────────


class TestRouteJob:
    def test_priority_interactive_always_returns_interactive(self):
        assert route_job(999_999_999, "application/pdf", "interactive") == QUEUE_INTERACTIVE

    def test_priority_batch_always_returns_batch(self):
        assert route_job(1, "application/pdf", "batch") == QUEUE_BATCH

    def test_priority_urgent_raises_value_error(self):
        with pytest.raises(ValueError, match="interactive.*batch"):
            route_job(100, "application/pdf", "urgent")

    def test_audio_mime_returns_batch(self):
        for mime in AUDIO_MIME_TYPES:
            assert route_job(100, mime, None) == QUEUE_BATCH

    def test_small_file_returns_interactive(self):
        # 1 KB — well below default 10 MB threshold
        assert route_job(1024, "application/pdf", None) == QUEUE_INTERACTIVE

    def test_large_file_returns_batch(self):
        # 20 MB — well above default 10 MB threshold
        assert route_job(20 * 1024 * 1024, "application/pdf", None) == QUEUE_BATCH

    def test_small_audio_returns_batch(self):
        # Audio overrides size: even tiny audio goes to batch
        assert route_job(100, "audio/mpeg", None) == QUEUE_BATCH

    def test_routing_error_falls_back_to_default(self):
        # Force settings to raise by patching ASYNC_SIZE_THRESHOLD_MB
        with patch("distill_app.queues.settings") as mock_settings:
            mock_settings.ASYNC_SIZE_THRESHOLD_MB = property(
                lambda self: (_ for _ in ()).throw(RuntimeError("bad config"))
            )
            # The fallback catches the exception
            result = route_job(1024, "application/pdf", None)
            assert result == QUEUE_DEFAULT


# ── API endpoint (Tests 9-14) ─────────────────────────────────────────────


def _mock_convert_result():
    from distill.quality import QualityScore

    qs = QualityScore(
        overall=0.9,
        heading_preservation=1.0,
        table_preservation=1.0,
        list_preservation=1.0,
        token_reduction_ratio=0.8,
    )
    r = MagicMock()
    r.markdown = "# Test"
    r.quality_score = 0.9
    r.quality_details = qs
    r.warnings = []
    r.structured_warnings = []
    m = MagicMock()
    m.word_count = 5
    m.page_count = 1
    m.slide_count = None
    m.sheet_count = None
    m.source_format = "docx"
    r.metadata = m
    r.chunks = None
    r.document_json = None
    r.html = None
    r.extracted = None
    return r


class TestAPIQueueRouting:
    @pytest.fixture()
    def client(self):
        from distill_app.server import build_app
        return TestClient(build_app())

    def test_priority_interactive_returns_queue_in_async_response(self, client):
        with patch("distill.convert", return_value=_mock_convert_result()), \
             patch("distill_app.server.should_run_async", return_value=True), \
             patch("distill_app.server.job_store") as mock_store, \
             patch("distill_app.worker.convert_document") as mock_task:
            mock_store.set_queued = MagicMock()
            mock_task.apply_async = MagicMock()

            files = {"file": ("report.docx", io.BytesIO(b"fake"), "application/octet-stream")}
            data = {"priority": "interactive"}
            resp = client.post("/api/convert", data=data, files=files)

        assert resp.status_code == 202
        body = resp.json()
        assert body["queue"] == QUEUE_INTERACTIVE

    def test_priority_batch_returns_batch_queue(self, client):
        with patch("distill.convert", return_value=_mock_convert_result()), \
             patch("distill_app.server.should_run_async", return_value=True), \
             patch("distill_app.server.job_store") as mock_store, \
             patch("distill_app.worker.convert_document") as mock_task:
            mock_store.set_queued = MagicMock()
            mock_task.apply_async = MagicMock()

            files = {"file": ("report.docx", io.BytesIO(b"fake"), "application/octet-stream")}
            data = {"priority": "batch"}
            resp = client.post("/api/convert", data=data, files=files)

        assert resp.status_code == 202
        body = resp.json()
        assert body["queue"] == QUEUE_BATCH

    def test_priority_urgent_returns_422(self, client):
        files = {"file": ("report.docx", io.BytesIO(b"fake"), "application/octet-stream")}
        data = {"priority": "urgent"}
        resp = client.post("/api/convert", data=data, files=files)
        assert resp.status_code == 422

    def test_no_priority_returns_queue_in_async_response(self, client):
        with patch("distill.convert", return_value=_mock_convert_result()), \
             patch("distill_app.server.should_run_async", return_value=True), \
             patch("distill_app.server.job_store") as mock_store, \
             patch("distill_app.worker.convert_document") as mock_task:
            mock_store.set_queued = MagicMock()
            mock_task.apply_async = MagicMock()

            files = {"file": ("report.docx", io.BytesIO(b"fake"), "application/octet-stream")}
            resp = client.post("/api/convert", data={}, files=files)

        assert resp.status_code == 202
        body = resp.json()
        assert "queue" in body

    def test_get_job_includes_queue_field(self, client):
        import distill_app.server as srv
        from distill_app.jobs import JobStore

        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = JobStore(redis_url="redis://fake:6379/0")
            store._redis = fakeredis.FakeRedis(decode_responses=True)
            store.set_queued("queue-test-job")
            store.set_processing("queue-test-job", queue=QUEUE_INTERACTIVE)
            srv.job_store = store
            resp = client.get("/jobs/queue-test-job")
            assert resp.status_code == 200
            body = resp.json()
            assert "queue" in body
            assert body["queue"] == QUEUE_INTERACTIVE
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_get_job_queue_matches_submission(self, client):
        import distill_app.server as srv
        from distill_app.jobs import JobStore

        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = JobStore(redis_url="redis://fake:6379/0")
            store._redis = fakeredis.FakeRedis(decode_responses=True)
            store.set_queued("match-job")
            store.set_processing("match-job", queue=QUEUE_BATCH)
            srv.job_store = store
            resp = client.get("/jobs/match-job")
            assert resp.status_code == 200
            assert resp.json()["queue"] == QUEUE_BATCH
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store


# ── Worker (Tests 15-16) ──────────────────────────────────────────────────


class TestWorkerQueueName:
    def test_convert_document_stores_queue_name(self):
        from distill_app.jobs import JobStatus, JobStore

        store = JobStore(redis_url="redis://fake:6379/0")
        store._redis = fakeredis.FakeRedis(decode_responses=True)
        store.set_queued("worker-q1")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", return_value=_mock_convert_result()):
            from distill_app.worker import convert_document
            convert_document(
                "worker-q1", tmp.name,
                {"max_table_rows": 500, "output_format": "markdown", "extra": {}},
                queue_name=QUEUE_BATCH,
            )

        job = store.get("worker-q1")
        assert job.queue == QUEUE_BATCH

    def test_job_queue_field_accessible_in_response(self):
        from distill_app.jobs import JobStore

        store = JobStore(redis_url="redis://fake:6379/0")
        store._redis = fakeredis.FakeRedis(decode_responses=True)
        store.set_queued("worker-q2")
        store.set_processing("worker-q2", queue=QUEUE_INTERACTIVE)

        job = store.get("worker-q2")
        assert job is not None
        assert job.queue == QUEUE_INTERACTIVE
