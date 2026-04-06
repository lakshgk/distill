"""
Tests for SSE job progress streaming: ProgressPublisher, worker
instrumentation, audio instrumentation, and SSE endpoint.

Uses fakeredis and mocks — no live Redis required.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import fakeredis
import pytest
from fastapi.testclient import TestClient

from distill_app.progress import ProgressEvent, ProgressPublisher, progress_channel
from distill_app.jobs import JobStatus, JobStore


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_fakeredis_store() -> JobStore:
    store = JobStore(redis_url="redis://fake:6379/0")
    store._redis = fakeredis.FakeRedis(decode_responses=True)
    return store


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


# ── ProgressPublisher tests (1-8) ───────────────────────────────────────────

class TestProgressChannel:
    def test_channel_naming(self):
        assert progress_channel("abc") == "distill.progress.abc"


class TestProgressEvent:
    def test_to_dict_always_includes_required_fields(self):
        e = ProgressEvent(job_id="j1", status="processing", queue="distill.interactive")
        d = e.to_dict()
        assert "job_id" in d
        assert "status" in d
        assert "queue" in d
        assert "ts" in d

    def test_to_dict_omits_none_optional_fields(self):
        e = ProgressEvent(job_id="j1", status="processing", queue="q")
        d = e.to_dict()
        assert "stage" not in d
        assert "pct" not in d
        assert "message" not in d

    def test_to_dict_includes_optional_fields_when_set(self):
        e = ProgressEvent(
            job_id="j1", status="processing", queue="q",
            stage="parsing", pct=50, message="Reading file",
        )
        d = e.to_dict()
        assert d["stage"] == "parsing"
        assert d["pct"] == 50
        assert d["message"] == "Reading file"


class TestProgressPublisher:
    def test_emit_does_not_raise_when_redis_unreachable(self):
        pub = ProgressPublisher(
            redis_url="redis://localhost:19999", job_id="j1", queue="q",
        )
        pub.emit("processing", stage="parsing", pct=10)
        pub.close()

    def test_emit_publishes_to_correct_channel(self):
        pub = ProgressPublisher(
            redis_url="redis://fake:6379/0", job_id="j1", queue="q",
        )
        mock_redis = MagicMock()
        pub._redis = mock_redis
        pub._available = True

        pub.emit("processing", stage="parsing", pct=30)

        mock_redis.publish.assert_called_once()
        channel, payload = mock_redis.publish.call_args[0]
        assert channel == "distill.progress.j1"
        data = json.loads(payload)
        assert data["status"] == "processing"
        assert data["pct"] == 30

    def test_emit_sets_available_false_after_failure(self):
        pub = ProgressPublisher(
            redis_url="redis://localhost:19999", job_id="j1", queue="q",
        )
        # First emit will fail and set _available = False
        pub.emit("processing")
        assert pub._available is False

    def test_close_does_not_raise_when_never_connected(self):
        pub = ProgressPublisher(
            redis_url="redis://fake:6379/0", job_id="j1", queue="q",
        )
        pub.close()  # should not raise


# ── Worker instrumentation tests (9-11) ─────────────────────────────────────

class TestWorkerProgress:
    def test_successful_job_emits_at_least_4_events(self):
        store = _make_fakeredis_store()
        store.set_queued("wp-1")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()

        mock_publisher = MagicMock()

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", return_value=_mock_convert_result()), \
             patch("distill_app.worker.ProgressPublisher", return_value=mock_publisher):
            from distill_app.worker import convert_document
            convert_document(
                "wp-1", tmp.name,
                {"max_table_rows": 500, "output_format": "markdown", "extra": {}},
            )

        assert mock_publisher.emit.call_count >= 4

    def test_failed_job_emits_failed_event(self):
        store = _make_fakeredis_store()
        store.set_queued("wp-2")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()

        from distill.parsers.base import DistillError

        mock_publisher = MagicMock()

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", side_effect=DistillError("bad")), \
             patch("distill_app.worker.ProgressPublisher", return_value=mock_publisher):
            from distill_app.worker import convert_document
            with pytest.raises(DistillError):
                convert_document(
                    "wp-2", tmp.name,
                    {"max_table_rows": 500, "output_format": "markdown", "extra": {}},
                )

        # Check that a "failed" emit was made
        failed_calls = [
            c for c in mock_publisher.emit.call_args_list
            if c[0][0] == "failed" or (c[1] and c[1].get("status") == "failed")
        ]
        assert len(failed_calls) >= 1

    def test_publisher_close_called_even_on_failure(self):
        store = _make_fakeredis_store()
        store.set_queued("wp-3")

        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(b"fake")
        tmp.close()

        from distill.parsers.base import DistillError

        mock_publisher = MagicMock()

        with patch("distill_app.worker.job_store", store), \
             patch("distill.convert", side_effect=DistillError("bad")), \
             patch("distill_app.worker.ProgressPublisher", return_value=mock_publisher):
            from distill_app.worker import convert_document
            with pytest.raises(DistillError):
                convert_document(
                    "wp-3", tmp.name,
                    {"max_table_rows": 500, "output_format": "markdown", "extra": {}},
                )

        mock_publisher.close.assert_called_once()


# ── Audio instrumentation tests (12-13) ─────────────────────────────────────

class TestAudioProgress:
    def test_audio_parser_emits_progress_when_publisher_present(self):
        mock_publisher = MagicMock()
        mock_segments = [MagicMock(text="Hello world", start=0.0, end=1.0, speaker=None)]

        with patch("distill.audio.quality.AudioQualityChecker") as MockChecker, \
             patch("distill.audio.transcription.TranscriberFactory") as MockFactory, \
             patch("distill.audio.diarization.SpeakerDiarizer") as MockDiarizer:
            MockChecker.return_value.check = MagicMock()
            MockFactory.create.return_value.transcribe.return_value = mock_segments
            MockDiarizer.return_value.diarize.return_value = mock_segments

            from distill.parsers.audio import AudioParser
            from distill.parsers.base import ParseOptions

            parser = AudioParser()
            options = ParseOptions(extra={"progress_publisher": mock_publisher})

            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(b"fake")
            tmp.close()

            parser.parse(tmp.name, options)

        assert mock_publisher.emit.call_count >= 4

    def test_audio_parser_works_without_publisher(self):
        mock_segments = [MagicMock(text="Hello", start=0.0, end=1.0, speaker=None)]

        with patch("distill.audio.quality.AudioQualityChecker") as MockChecker, \
             patch("distill.audio.transcription.TranscriberFactory") as MockFactory, \
             patch("distill.audio.diarization.SpeakerDiarizer") as MockDiarizer:
            MockChecker.return_value.check = MagicMock()
            MockFactory.create.return_value.transcribe.return_value = mock_segments
            MockDiarizer.return_value.diarize.return_value = mock_segments

            from distill.parsers.audio import AudioParser
            from distill.parsers.base import ParseOptions

            parser = AudioParser()
            options = ParseOptions()
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(b"fake")
            tmp.close()

            doc = parser.parse(tmp.name, options)

        assert doc is not None
        assert len(doc.sections) > 0


# ── SSE endpoint tests (14-19) ──────────────────────────────────────────────

class TestSSEEndpoint:
    @pytest.fixture()
    def client(self):
        from distill_app.server import build_app
        return TestClient(build_app())

    def test_unknown_job_returns_404(self, client):
        import distill_app.server as srv
        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            srv.job_store = _make_fakeredis_store()
            resp = client.get("/jobs/nonexistent/stream")
            assert resp.status_code == 404
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_content_type_is_event_stream(self, client):
        import distill_app.server as srv
        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = _make_fakeredis_store()
            store.set_queued("sse-ct")
            store.set_complete("sse-ct", {"markdown": "# Done"})
            srv.job_store = store
            resp = client.get("/jobs/sse-ct/stream")
            assert "text/event-stream" in resp.headers["content-type"]
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_completed_job_sends_final_event_and_closes(self, client):
        import distill_app.server as srv
        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = _make_fakeredis_store()
            store.set_queued("sse-done")
            store.set_complete("sse-done", {"markdown": "# Done"})
            srv.job_store = store
            resp = client.get("/jobs/sse-done/stream")
            assert resp.status_code == 200
            body = resp.text
            assert "data:" in body
            # Parse the event
            for line in body.strip().split("\n"):
                if line.startswith("data:"):
                    data = json.loads(line[len("data:"):].strip())
                    assert data["status"] == "complete"
                    assert data["pct"] == 100
                    break
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_heartbeat_sent_when_no_event_arrives(self, client):
        import distill_app.server as srv

        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = _make_fakeredis_store()
            store.set_queued("sse-hb")
            store.set_processing("sse-hb")
            srv.job_store = store

            mock_pubsub = AsyncMock()
            mock_pubsub.get_message = AsyncMock(return_value=None)
            mock_pubsub.subscribe = AsyncMock()
            mock_pubsub.unsubscribe = AsyncMock()
            mock_pubsub.close = AsyncMock()

            mock_redis_async = MagicMock()
            mock_redis_async.pubsub.return_value = mock_pubsub
            mock_redis_async.close = AsyncMock()

            with patch.object(srv.settings, "SSE_MAX_DURATION_SECONDS", 1), \
                 patch.object(srv.settings, "SSE_KEEPALIVE_SECONDS", 0.1), \
                 patch("redis.asyncio.from_url", return_value=mock_redis_async):
                resp = client.get("/jobs/sse-hb/stream")

            assert ": heartbeat" in resp.text
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_stream_closes_after_max_duration(self, client):
        import distill_app.server as srv

        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = _make_fakeredis_store()
            store.set_queued("sse-timeout")
            store.set_processing("sse-timeout")
            srv.job_store = store

            mock_pubsub = AsyncMock()
            mock_pubsub.get_message = AsyncMock(return_value=None)
            mock_pubsub.subscribe = AsyncMock()
            mock_pubsub.unsubscribe = AsyncMock()
            mock_pubsub.close = AsyncMock()

            mock_redis_async = MagicMock()
            mock_redis_async.pubsub.return_value = mock_pubsub
            mock_redis_async.close = AsyncMock()

            with patch.object(srv.settings, "SSE_MAX_DURATION_SECONDS", 1), \
                 patch.object(srv.settings, "SSE_KEEPALIVE_SECONDS", 0.3), \
                 patch("redis.asyncio.from_url", return_value=mock_redis_async):
                resp = client.get("/jobs/sse-timeout/stream")

            assert "timeout" in resp.text
            assert "Stream duration limit reached" in resp.text
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_redis_unavailable_returns_error_event(self, client):
        import distill_app.server as srv

        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            store = _make_fakeredis_store()
            store.set_queued("sse-err")
            store.set_processing("sse-err")
            srv.job_store = store

            with patch("redis.asyncio.from_url", side_effect=ConnectionError("refused")):
                resp = client.get("/jobs/sse-err/stream")

            assert resp.status_code == 200
            assert "Progress stream unavailable" in resp.text
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store
