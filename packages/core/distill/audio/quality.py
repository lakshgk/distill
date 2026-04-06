"""
distill.audio.quality
~~~~~~~~~~~~~~~~~~~~~
Audio quality pre-check service.

Inspects audio metadata (duration, bitrate, channels, sample rate) and emits
AUDIO_QUALITY_LOW warnings for conditions that may affect transcription quality.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from distill.warnings import ConversionWarning, WarningCollector, WarningType

_logger = logging.getLogger(__name__)


class AudioQualityChecker:
    """Check audio file metadata and emit quality warnings."""

    def __init__(self, max_duration_seconds: int = 14400) -> None:
        self._max_duration = max_duration_seconds

    def check(self, file_path: str, collector: Optional[WarningCollector] = None) -> dict:
        """Inspect audio metadata and emit warnings for quality issues.

        Returns a dict of metadata or ``{}`` on any error. Never raises.
        """
        try:
            return self._check_impl(file_path, collector)
        except Exception as exc:
            _logger.debug("AudioQualityChecker.check error: %s", exc)
            if collector is not None:
                collector.add(ConversionWarning(
                    type=WarningType.AUDIO_QUALITY_LOW,
                    message="Could not inspect audio file metadata",
                ))
            return {}

    def _check_impl(self, file_path: str, collector: Optional[WarningCollector]) -> dict:
        from distill.parsers.base import AUDIO_IMPORT_ERROR

        try:
            import soundfile as sf
        except ImportError:
            raise ImportError(AUDIO_IMPORT_ERROR)

        try:
            import librosa
        except ImportError:
            raise ImportError(AUDIO_IMPORT_ERROR)

        info = sf.info(file_path)
        duration = librosa.get_duration(path=file_path)
        file_size = os.path.getsize(file_path)

        bitrate_kbps = int(file_size * 8 / max(duration, 0.001) / 1000)

        meta = {
            "duration_seconds": duration,
            "bitrate_kbps": bitrate_kbps,
            "channels": info.channels,
            "sample_rate": info.samplerate,
            "format": info.format,
        }

        if collector is not None:
            if bitrate_kbps < 32:
                collector.add(ConversionWarning(
                    type=WarningType.AUDIO_QUALITY_LOW,
                    message=f"Audio bitrate is very low ({bitrate_kbps} kbps). "
                            "Transcription quality may be degraded.",
                ))

            if duration > self._max_duration:
                collector.add(ConversionWarning(
                    type=WarningType.AUDIO_QUALITY_LOW,
                    message=f"Audio duration ({duration:.0f}s) exceeds the "
                            f"{self._max_duration}s limit. Processing may be slow.",
                ))

            if info.channels == 1 and info.samplerate <= 8000:
                collector.add(ConversionWarning(
                    type=WarningType.AUDIO_QUALITY_LOW,
                    message="Telephone-quality audio detected (mono, "
                            f"{info.samplerate} Hz). Transcription accuracy may "
                            "be reduced.",
                ))

        return meta
