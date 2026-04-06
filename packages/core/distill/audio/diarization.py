"""
distill.audio.diarization
~~~~~~~~~~~~~~~~~~~~~~~~~~
Speaker diarization service using pyannote.audio.

Assigns speaker labels ("Speaker A", "Speaker B", ...) to transcription
segments by matching each segment's midpoint to the speaker turn that covers
that timestamp.

All pyannote imports are lazy to allow the module to import cleanly without
the audio extras installed.
"""

from __future__ import annotations

import logging
from typing import Optional

from distill.audio.transcription import TranscriptionSegment
from distill.warnings import ConversionWarning, WarningCollector, WarningType

_logger = logging.getLogger(__name__)

_MODEL_MISSING_MSG = (
    "Speaker diarization model unavailable — transcripts will not include "
    "speaker labels. Set DISTILL_HF_TOKEN and accept the model licence at "
    "https://huggingface.co/pyannote/speaker-diarization-3.1"
)


class SpeakerDiarizer:
    """Assign speaker labels to transcription segments via pyannote."""

    def __init__(self, hf_token: Optional[str] = None) -> None:
        self.hf_token = hf_token
        self.available = False
        self._pipeline = None
        self._loaded = False

    def _load_pipeline(self) -> bool:
        if self._loaded:
            return self.available
        self._loaded = True

        try:
            from pyannote.audio import Pipeline
        except ImportError:
            self.available = False
            return False

        try:
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.hf_token,
            )
            self.available = True
            return True
        except Exception as exc:
            _logger.debug("pyannote pipeline load failed: %s", exc)
            self.available = False
            return False

    def diarize(
        self,
        file_path: str,
        segments: list[TranscriptionSegment],
        collector: WarningCollector,
    ) -> list[TranscriptionSegment]:
        """Assign speaker labels to segments. Never raises.

        If the model is unavailable, returns segments unchanged and emits
        an ``AUDIO_MODEL_MISSING`` warning.
        """
        if not self._loaded:
            self._load_pipeline()

        if not self.available:
            collector.add(ConversionWarning(
                type=WarningType.AUDIO_MODEL_MISSING,
                message=_MODEL_MISSING_MSG,
            ))
            return segments

        try:
            return self._diarize_impl(file_path, segments)
        except Exception as exc:
            _logger.debug("SpeakerDiarizer.diarize failed: %s", exc)
            self.available = False
            collector.add(ConversionWarning(
                type=WarningType.AUDIO_MODEL_MISSING,
                message=_MODEL_MISSING_MSG,
            ))
            return segments

    def _diarize_impl(
        self,
        file_path: str,
        segments: list[TranscriptionSegment],
    ) -> list[TranscriptionSegment]:
        diarization = self._pipeline(file_path)

        # Build speaker turns: list of (start, end, raw_speaker_label)
        turns: list[tuple[float, float, str]] = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            turns.append((turn.start, turn.end, speaker))

        # Map raw speaker labels to deterministic "Speaker A", "Speaker B", ...
        label_map: dict[str, str] = {}
        label_counter = 0
        for _, _, raw_label in turns:
            if raw_label not in label_map:
                letter = chr(ord("A") + label_counter)
                label_map[raw_label] = f"Speaker {letter}"
                label_counter += 1

        # Assign speaker to each segment by midpoint matching
        for seg in segments:
            start = seg.start if seg.start is not None else 0.0
            end = seg.end if seg.end is not None else start
            midpoint = (start + end) / 2.0

            best_speaker = None
            for t_start, t_end, raw_label in turns:
                if t_start <= midpoint <= t_end:
                    best_speaker = label_map[raw_label]
                    break

            seg.speaker = best_speaker

        return segments
