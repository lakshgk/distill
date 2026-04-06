"""Generate audio test fixtures using stdlib only (wave + struct)."""

import struct
import wave
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


def generate_wav(filename: str, duration_s: float, sample_rate: int, channels: int = 1):
    """Generate a WAV file filled with silence."""
    path = FIXTURES_DIR / filename
    n_frames = int(sample_rate * duration_s)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        # Write silence (all zeros)
        wf.writeframes(struct.pack(f"<{n_frames * channels}h", *([0] * n_frames * channels)))
    print(f"Created {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    # 3 seconds, 16kHz, mono — standard quality
    generate_wav("sample.wav", duration_s=3.0, sample_rate=16000)

    # 3 seconds, 8kHz, mono — telephone quality (triggers AUDIO_QUALITY_LOW)
    generate_wav("sample_low_bitrate.wav", duration_s=3.0, sample_rate=8000)

    # 1 second, 16kHz, mono — short fixture for fast tests
    generate_wav("sample_short.wav", duration_s=1.0, sample_rate=16000)

    print("All fixtures generated.")
