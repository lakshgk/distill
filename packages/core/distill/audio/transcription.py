"""
distill.audio.transcription
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Transcription engines for audio files.

Two backends are supported:
    - WhisperTranscriber (faster-whisper) — default, high quality
    - VoskTranscriber (vosk) — lightweight offline alternative

All audio library imports are lazy to allow the module to import cleanly
without the audio extras installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    text: str
    start: float
    end: float
    speaker: Optional[str] = None


class WhisperTranscriber:
    """Transcribe audio using faster-whisper."""

    def __init__(self, model_size: str = "medium") -> None:
        self.model_size = model_size
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        from distill.parsers.base import AUDIO_IMPORT_ERROR
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ImportError(AUDIO_IMPORT_ERROR)
        self._model = WhisperModel(self.model_size)

    def transcribe(self, file_path: str) -> list[TranscriptionSegment]:
        """Transcribe audio file and return segments. Never raises."""
        try:
            return self._transcribe_impl(file_path)
        except Exception as exc:
            _logger.error("WhisperTranscriber.transcribe failed: %s", exc)
            return []

    def _transcribe_impl(self, file_path: str) -> list[TranscriptionSegment]:
        from distill.parsers.base import AUDIO_IMPORT_ERROR

        self._load_model()

        # Check duration for long audio splitting
        # librosa/soundfile may not support all formats (e.g. m4a/aac)
        # — fall through to direct transcription if duration check fails
        duration = None
        try:
            import librosa
            duration = librosa.get_duration(path=file_path)
        except ImportError:
            raise ImportError(AUDIO_IMPORT_ERROR)
        except Exception:
            pass  # format not supported by soundfile — skip chunking

        if duration is not None and duration > 600:
            return self._transcribe_chunked(file_path)

        segments_iter, _ = self._model.transcribe(file_path, word_timestamps=True)
        return [
            TranscriptionSegment(
                text=(seg.text or "").strip(),
                start=seg.start,
                end=seg.end,
            )
            for seg in segments_iter
            if (seg.text or "").strip()
        ]

    def _transcribe_chunked(self, file_path: str) -> list[TranscriptionSegment]:
        """Split long audio at silence boundaries before transcribing."""
        from distill.parsers.base import AUDIO_IMPORT_ERROR
        try:
            from pydub import AudioSegment
            from pydub.silence import detect_nonsilent
        except ImportError:
            raise ImportError(AUDIO_IMPORT_ERROR)

        audio = AudioSegment.from_file(file_path)
        nonsilent_ranges = detect_nonsilent(audio, min_silence_len=500, silence_thresh=-40)

        segments: list[TranscriptionSegment] = []
        import tempfile
        for start_ms, end_ms in nonsilent_ranges:
            chunk = audio[start_ms:end_ms]
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                chunk.export(tmp.name, format="wav")
                chunk_segs, _ = self._model.transcribe(tmp.name, word_timestamps=True)
                offset = start_ms / 1000.0
                for seg in chunk_segs:
                    if (seg.text or "").strip():
                        segments.append(TranscriptionSegment(
                            text=(seg.text or "").strip(),
                            start=seg.start + offset,
                            end=seg.end + offset,
                        ))
                try:
                    import os
                    os.unlink(tmp.name)
                except Exception:
                    pass

        return segments


class VoskTranscriber:
    """Transcribe audio using Vosk (offline, lightweight)."""

    def __init__(self, model_path: str) -> None:
        self.model_path = model_path

    def transcribe(self, file_path: str) -> list[TranscriptionSegment]:
        """Transcribe audio file and return segments. Never raises."""
        try:
            return self._transcribe_impl(file_path)
        except Exception as exc:
            _logger.error("VoskTranscriber.transcribe failed: %s", exc)
            return []

    def _transcribe_impl(self, file_path: str) -> list[TranscriptionSegment]:
        import json as _json
        import wave
        from distill.parsers.base import AUDIO_IMPORT_ERROR

        try:
            import vosk
        except ImportError:
            raise ImportError(AUDIO_IMPORT_ERROR)

        # Convert to WAV if needed
        wav_path = file_path
        tmp_wav = None
        if not file_path.lower().endswith(".wav"):
            try:
                from pydub import AudioSegment
            except ImportError:
                raise ImportError(AUDIO_IMPORT_ERROR)
            import tempfile
            audio = AudioSegment.from_file(file_path)
            audio = audio.set_channels(1).set_frame_rate(16000)
            tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            audio.export(tmp_wav.name, format="wav")
            wav_path = tmp_wav.name

        try:
            model = vosk.Model(self.model_path)
            wf = wave.open(wav_path, "rb")
            rec = vosk.KaldiRecognizer(model, wf.getframerate())
            rec.SetWords(True)

            segments: list[TranscriptionSegment] = []
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                rec.AcceptWaveform(data)

            final = _json.loads(rec.FinalResult())
            text = final.get("text", "").strip()
            if text:
                segments.append(TranscriptionSegment(text=text, start=0.0, end=0.0))

            wf.close()
            return segments
        finally:
            if tmp_wav is not None:
                try:
                    import os
                    os.unlink(tmp_wav.name)
                except Exception:
                    pass


class TranscriberFactory:
    """Create the appropriate transcriber based on engine name."""

    @staticmethod
    def create(engine: str = "whisper", **kwargs) -> WhisperTranscriber | VoskTranscriber:
        if engine == "whisper":
            return WhisperTranscriber(model_size=kwargs.get("model_size", "medium"))
        if engine == "vosk":
            if "model_path" not in kwargs:
                raise ValueError("VoskTranscriber requires model_path")
            return VoskTranscriber(model_path=kwargs["model_path"])
        raise ValueError(
            f"Unknown transcription engine: {engine!r}. Supported: whisper, vosk"
        )
