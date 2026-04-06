"""
Tests for the audio pipeline: AudioQualityChecker, transcription services,
SpeakerDiarizer, AudioParser, and API integration.

All audio extras (faster_whisper, pyannote, librosa, soundfile) are mocked.
Tests pass without pip install distill-core[audio].
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from distill.audio.diarization import SpeakerDiarizer
from distill.audio.quality import AudioQualityChecker
from distill.audio.transcription import (
    TranscriberFactory,
    TranscriptionSegment,
    VoskTranscriber,
    WhisperTranscriber,
)
from distill.ir import Document, Paragraph, Section, Table
from distill.parsers.audio import AudioParser
from distill.parsers.base import ParseOptions
from distill.warnings import WarningCollector, WarningType

FIXTURES = Path(__file__).parent / "fixtures" / "audio"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_convert_result():
    from distill.quality import QualityScore
    qs = QualityScore(overall=0.9, heading_preservation=1.0,
                      table_preservation=1.0, list_preservation=1.0,
                      token_reduction_ratio=0.8)
    r = MagicMock()
    r.markdown = "# Audio\n\nTranscript."
    r.quality_score = 0.9
    r.quality_details = qs
    r.warnings = []
    r.structured_warnings = []
    m = MagicMock()
    m.word_count = 5
    m.page_count = None
    m.slide_count = None
    m.sheet_count = None
    m.source_format = "audio"
    r.metadata = m
    r.chunks = None
    r.document_json = None
    r.html = None
    r.extracted = None
    return r


def _make_segments(with_speakers=False):
    return [
        TranscriptionSegment(
            text="Hello everyone.",
            start=0.0, end=1.5,
            speaker="Speaker A" if with_speakers else None,
        ),
        TranscriptionSegment(
            text="Thanks for joining.",
            start=2.0, end=3.5,
            speaker="Speaker B" if with_speakers else None,
        ),
    ]


# ── AudioQualityChecker (Tests 1-4) ─────────────────────────────────────────

class TestAudioQualityChecker:
    def test_missing_file_returns_empty_dict_with_warning(self):
        collector = WarningCollector()
        meta = AudioQualityChecker().check("nonexistent.mp3", collector)
        assert meta == {}
        assert collector.has(WarningType.AUDIO_QUALITY_LOW)

    def test_low_bitrate_emits_warning(self):
        import sys
        mock_info = SimpleNamespace(channels=2, samplerate=44100, format="WAV")
        mock_sf = MagicMock()
        mock_sf.info.return_value = mock_info
        mock_lr = MagicMock()
        mock_lr.get_duration.return_value = 60.0
        collector = WarningCollector()
        with patch.dict(sys.modules, {"soundfile": mock_sf, "librosa": mock_lr}), \
             patch("os.path.getsize", return_value=75000):
            meta = AudioQualityChecker().check("test.wav", collector)
        assert collector.has(WarningType.AUDIO_QUALITY_LOW)
        assert any("bitrate" in w.message.lower() for w in collector.all())

    def test_telephone_quality_emits_warning(self):
        import sys
        mock_info = SimpleNamespace(channels=1, samplerate=8000, format="WAV")
        mock_sf = MagicMock()
        mock_sf.info.return_value = mock_info
        mock_lr = MagicMock()
        mock_lr.get_duration.return_value = 10.0
        collector = WarningCollector()
        with patch.dict(sys.modules, {"soundfile": mock_sf, "librosa": mock_lr}), \
             patch("os.path.getsize", return_value=160000):
            meta = AudioQualityChecker().check("test.wav", collector)
        assert any("telephone" in w.message.lower() for w in collector.all())

    def test_never_raises(self):
        collector = WarningCollector()
        # Missing file + missing extras → should return {} without raising
        meta = AudioQualityChecker().check("nonexistent_really.wav", collector)
        assert isinstance(meta, dict)


# ── Transcription (Tests 5-8) ───────────────────────────────────────────────

class TestTranscription:
    def test_factory_creates_whisper(self):
        t = TranscriberFactory.create("whisper", model_size="tiny")
        assert isinstance(t, WhisperTranscriber)
        assert t.model_size == "tiny"

    def test_factory_creates_vosk(self):
        t = TranscriberFactory.create("vosk", model_path="/tmp/model")
        assert isinstance(t, VoskTranscriber)

    def test_factory_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            TranscriberFactory.create("unknown")

    def test_whisper_returns_empty_on_error(self):
        t = WhisperTranscriber(model_size="tiny")
        # _load_model will fail since faster_whisper isn't installed
        result = t.transcribe("nonexistent.wav")
        assert result == []


# ── SpeakerDiarizer (Tests 9-12) ────────────────────────────────────────────

class TestSpeakerDiarizer:
    def test_no_token_not_available(self):
        d = SpeakerDiarizer(hf_token=None)
        assert not d.available

    def test_unavailable_returns_unchanged_with_warning(self):
        d = SpeakerDiarizer(hf_token=None)
        collector = WarningCollector()
        segs = _make_segments()
        result = d.diarize("dummy.mp3", segs, collector)
        assert len(result) == 2
        assert all(s.speaker is None for s in result)
        assert collector.has(WarningType.AUDIO_MODEL_MISSING)

    def test_warning_contains_hf_token_guidance(self):
        d = SpeakerDiarizer(hf_token=None)
        collector = WarningCollector()
        d.diarize("dummy.mp3", _make_segments(), collector)
        w = collector.to_dict()[0]
        assert "DISTILL_HF_TOKEN" in w["message"]

    def test_assigns_speaker_labels_when_available(self):
        d = SpeakerDiarizer(hf_token="fake-token")
        d._loaded = True
        d.available = True

        # Mock pyannote diarization output
        mock_turn_a = MagicMock()
        mock_turn_a.start = 0.0
        mock_turn_a.end = 2.0
        mock_turn_b = MagicMock()
        mock_turn_b.start = 2.0
        mock_turn_b.end = 4.0

        mock_diarization = MagicMock()
        mock_diarization.itertracks.return_value = [
            (mock_turn_a, None, "SPEAKER_00"),
            (mock_turn_b, None, "SPEAKER_01"),
        ]
        d._pipeline = MagicMock(return_value=mock_diarization)

        collector = WarningCollector()
        segs = _make_segments()
        result = d.diarize("test.wav", segs, collector)

        assert result[0].speaker == "Speaker A"
        assert result[1].speaker == "Speaker B"


# ── AudioParser (Tests 13-19) ───────────────────────────────────────────────

class TestAudioParser:
    def _parse_with_mock_segments(self, segments, file_path=None):
        """Parse using mocked transcriber returning given segments."""
        file_path = file_path or str(FIXTURES / "sample.wav")
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = segments

        with patch("distill.audio.transcription.TranscriberFactory.create",
                   return_value=mock_transcriber), \
             patch("distill.audio.quality.AudioQualityChecker.check",
                   return_value={}):
            parser = AudioParser()
            return parser.parse(file_path, ParseOptions())

    def test_returns_document_with_sections(self):
        doc = self._parse_with_mock_segments(_make_segments())
        assert isinstance(doc, Document)
        assert len(doc.sections) >= 1

    def test_heading_contains_filename_stem(self):
        doc = self._parse_with_mock_segments(_make_segments())
        heading_text = "".join(r.text for s in doc.sections
                               for r in (s.heading or []))
        assert "sample" in heading_text.lower()

    def test_speaker_format_with_labels(self):
        doc = self._parse_with_mock_segments(_make_segments(with_speakers=True))
        all_text = " ".join(
            r.text for s in doc.sections for b in s.blocks
            if isinstance(b, Paragraph) for r in b.runs
        )
        assert "Speaker A:" in all_text

    def test_format_without_speakers(self):
        doc = self._parse_with_mock_segments(_make_segments())
        all_text = " ".join(
            r.text for s in doc.sections for b in s.blocks
            if isinstance(b, Paragraph) for r in b.runs
        )
        assert "**[00:00]:**" in all_text

    def test_empty_text_skipped(self):
        segs = [
            TranscriptionSegment(text="", start=0.0, end=1.0),
            TranscriptionSegment(text="Hello.", start=1.0, end=2.0),
        ]
        doc = self._parse_with_mock_segments(segs)
        para_count = sum(
            1 for s in doc.sections for b in s.blocks if isinstance(b, Paragraph)
        )
        assert para_count == 1

    def test_silence_gap_creates_new_section(self):
        segs = [
            TranscriptionSegment(text="Part one.", start=0.0, end=5.0),
            TranscriptionSegment(text="Part two.", start=40.0, end=45.0),
        ]
        doc = self._parse_with_mock_segments(segs)
        assert len(doc.sections) == 2

    def test_empty_transcription_returns_no_speech(self):
        doc = self._parse_with_mock_segments([])
        all_text = " ".join(
            r.text for s in doc.sections for b in s.blocks
            if isinstance(b, Paragraph) for r in b.runs
        )
        assert "no speech" in all_text.lower()


# ── API integration (Tests 20-25) ───────────────────────────────────────────

class TestAudioAPI:
    @pytest.fixture()
    def client(self):
        from distill_app.server import build_app
        from fastapi.testclient import TestClient
        return TestClient(build_app())

    def test_audio_returns_202_when_redis_healthy(self, client):
        import distill_app.server as srv
        orig_healthy = srv._redis_healthy
        orig_store = srv.job_store
        try:
            srv._redis_healthy = True
            srv.job_store = MagicMock()
            srv.job_store.set_queued = MagicMock()
            with patch("distill_app.worker.convert_document") as mock_task:
                mock_task.delay = MagicMock()
                files = {"file": ("test.mp3", io.BytesIO(b"fake"), "audio/mpeg")}
                resp = client.post("/api/convert", data={}, files=files)
            assert resp.status_code == 202
            assert "job_id" in resp.json()
        finally:
            srv._redis_healthy = orig_healthy
            srv.job_store = orig_store

    def test_audio_returns_503_when_redis_unhealthy(self, client):
        import distill_app.server as srv
        orig = srv._redis_healthy
        try:
            srv._redis_healthy = False
            files = {"file": ("test.mp3", io.BytesIO(b"fake"), "audio/mpeg")}
            resp = client.post("/api/convert", data={}, files=files)
            assert resp.status_code == 503
        finally:
            srv._redis_healthy = orig

    def test_audio_json_output_returns_422(self, client):
        import distill_app.server as srv
        orig = srv._redis_healthy
        try:
            srv._redis_healthy = True
            files = {"file": ("test.mp3", io.BytesIO(b"fake"), "audio/mpeg")}
            data = {"output_format": "json"}
            resp = client.post("/api/convert", data=data, files=files)
            assert resp.status_code == 422
        finally:
            srv._redis_healthy = orig

    def test_audio_html_output_returns_422(self, client):
        import distill_app.server as srv
        orig = srv._redis_healthy
        try:
            srv._redis_healthy = True
            files = {"file": ("test.mp3", io.BytesIO(b"fake"), "audio/mpeg")}
            data = {"output_format": "html"}
            resp = client.post("/api/convert", data=data, files=files)
            assert resp.status_code == 422
        finally:
            srv._redis_healthy = orig

    def test_unknown_engine_returns_422(self, client):
        import distill_app.server as srv
        orig = srv._redis_healthy
        try:
            srv._redis_healthy = True
            files = {"file": ("test.mp3", io.BytesIO(b"fake"), "audio/mpeg")}
            data = {"transcription_engine": "unknown"}
            resp = client.post("/api/convert", data=data, files=files)
            assert resp.status_code == 422
        finally:
            srv._redis_healthy = orig

    def test_vosk_without_model_path_returns_422(self, client):
        import distill_app.server as srv
        orig = srv._redis_healthy
        try:
            srv._redis_healthy = True
            files = {"file": ("test.mp3", io.BytesIO(b"fake"), "audio/mpeg")}
            data = {"transcription_engine": "vosk"}
            resp = client.post("/api/convert", data=data, files=files)
            assert resp.status_code == 422
        finally:
            srv._redis_healthy = orig
