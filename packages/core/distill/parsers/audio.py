"""
distill.parsers.audio
~~~~~~~~~~~~~~~~~~~~~
Parser for audio files (.mp3, .wav, .m4a, .flac, .ogg).

Pipeline: AudioQualityChecker → Transcriber → SpeakerDiarizer → IR mapping

Audio is always processed asynchronously via the Celery worker. The synchronous
``convert()`` path raises ``ParseError`` for audio input.

All audio library imports are lazy — the module imports cleanly without the
audio extras installed.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from distill.ir import Document, DocumentMetadata, Paragraph, Section, TextRun
from distill.parsers.base import ParseError, ParseOptions, Parser
from distill.registry import registry

_logger = logging.getLogger(__name__)


def _format_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS."""
    if seconds is None:
        seconds = 0.0
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


@registry.register
class AudioParser(Parser):
    """Parses audio files via transcription + optional speaker diarization."""

    extensions = [".mp3", ".wav", ".m4a", ".flac", ".ogg"]
    mime_types = [
        "audio/mpeg", "audio/wav", "audio/mp4",
        "audio/flac", "audio/ogg",
    ]
    requires = []  # extras checked lazily at parse time

    def parse(
        self,
        source: Union[str, Path, bytes],
        options: Optional[ParseOptions] = None,
    ) -> Document:
        options = options or ParseOptions()

        # Resolve file path
        if isinstance(source, bytes):
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(source)
            tmp.close()
            file_path = tmp.name
            created_tmp = True
        else:
            file_path = str(source)
            created_tmp = False

        try:
            return self._parse_impl(file_path, options)
        except ParseError:
            raise
        except Exception as exc:
            _logger.error("AudioParser.parse failed: %s", exc)
            return Document(
                metadata=DocumentMetadata(source_format="audio"),
                sections=[Section(level=0, blocks=[
                    Paragraph(runs=[TextRun(text=f"Audio processing failed: {exc}")])
                ])],
            )
        finally:
            if created_tmp:
                try:
                    os.unlink(file_path)
                except Exception:
                    pass

    def _parse_impl(self, file_path: str, options: ParseOptions) -> Document:
        from distill.audio.quality import AudioQualityChecker
        from distill.audio.transcription import TranscriberFactory, TranscriptionSegment
        from distill.audio.diarization import SpeakerDiarizer
        from distill.warnings import WarningCollector

        collector = options.collector or WarningCollector()

        # Progress publisher (optional, passed from app layer via options.extra)
        publisher = (
            options.extra.get("progress_publisher")
            if options and options.extra
            else None
        )

        # 1. Quality pre-check
        checker = AudioQualityChecker()
        checker.check(file_path, collector)

        if publisher:
            try:
                publisher.emit("processing", stage="audio_quality_check", pct=5)
            except Exception:
                pass

        # 2. Transcription
        engine = getattr(options, "transcription_engine", "whisper")
        model_size = getattr(options, "whisper_model", "medium")

        if publisher:
            try:
                publisher.emit(
                    "processing", stage="transcription", pct=10,
                    message=f"Transcribing with {engine}",
                )
            except Exception:
                pass

        transcriber = TranscriberFactory.create(engine, model_size=model_size)
        segments = transcriber.transcribe(file_path)

        if publisher:
            try:
                publisher.emit("processing", stage="transcription", pct=70)
            except Exception:
                pass

        if not segments:
            return Document(
                metadata=self._build_metadata(file_path),
                sections=[Section(level=0, blocks=[
                    Paragraph(runs=[TextRun(text="No speech detected in audio file.")])
                ])],
            )

        # 3. Speaker diarization
        if publisher:
            try:
                publisher.emit("processing", stage="diarization", pct=75)
            except Exception:
                pass

        hf_token = getattr(options, "hf_token", None)
        if not hf_token:
            try:
                from distill_app import settings
                hf_token = settings.HF_TOKEN or None
            except ImportError:
                pass
        diarizer = SpeakerDiarizer(hf_token=hf_token)
        segments = diarizer.diarize(file_path, segments, collector)

        if publisher:
            try:
                publisher.emit("processing", stage="diarization", pct=80)
            except Exception:
                pass

        # 4. Topic segmentation (if enabled)
        if publisher and getattr(options, "topic_segmentation", False):
            try:
                publisher.emit("processing", stage="topic_segmentation", pct=82)
            except Exception:
                pass

        # 5. IR mapping
        return self._map_to_ir(file_path, segments)

    def _build_metadata(self, file_path: str) -> DocumentMetadata:
        stem = Path(file_path).stem
        try:
            mtime = os.path.getmtime(file_path)
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except Exception:
            date_str = None

        title = f"{stem} — {date_str}" if date_str else stem

        return DocumentMetadata(
            title=title,
            source_format="audio",
            source_path=file_path,
        )

    def _map_to_ir(
        self, file_path: str, segments: list
    ) -> Document:
        metadata = self._build_metadata(file_path)
        doc = Document(metadata=metadata)

        # Document-level heading
        main_section = Section(
            level=1,
            heading=[TextRun(text=metadata.title or Path(file_path).stem)],
            blocks=[],
        )

        current_section = main_section
        prev_end = 0.0

        for seg in segments:
            text = (seg.text or "").strip() if seg.text else ""
            if not text:
                continue

            start = seg.start if seg.start is not None else 0.0
            end = seg.end if seg.end is not None else start

            # Long silence detection: > 30 seconds gap → new section
            if start - prev_end > 30 and current_section.blocks:
                doc.sections.append(current_section)
                current_section = Section(
                    level=2,
                    heading=[TextRun(text=f"[{_format_timestamp(start)}]")],
                    blocks=[],
                )

            # Format paragraph content
            ts = _format_timestamp(start)
            speaker = seg.speaker if seg.speaker is not None else None
            if speaker:
                prefix = f"**[{ts}] {speaker}:**"
            else:
                prefix = f"**[{ts}]:**"

            current_section.blocks.append(
                Paragraph(runs=[TextRun(text=f"{prefix} {text}")])
            )

            prev_end = end

        if current_section.blocks or current_section.heading:
            doc.sections.append(current_section)

        return doc
