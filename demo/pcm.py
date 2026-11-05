"""Placeholder PCM and WAV writing.

Local and loopback runs do not execute Qwen3-TTS. They still emit real
PCM packets so first-audio time is measured on an actual buffer, then
stored as WAV. One packet is one 12.5 Hz codec frame (1920 samples at
24 kHz, 80 ms of audio). That length matches the model's frame rate; it
is not a measured model decode time.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

SAMPLE_RATE = 24000
FRAME_HZ = 12.5
FRAME_SAMPLES = int(SAMPLE_RATE / FRAME_HZ)  # 1920


def render_placeholder_pcm(index: int, *, frames: int = 1) -> bytes:
    n = FRAME_SAMPLES * frames
    freq = 440.0 + (index % 12) * 35.0
    amp = 0.2
    samples = array.array("h")
    for i in range(n):
        value = int(amp * 32767.0 * math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE))
        samples.append(value)
    return samples.tobytes()


def write_wav(path: Path, pcm_s16le: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm_s16le)


def prepare_out_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for old in path.glob("chunk_*.wav"):
        old.unlink()
    for name in ("growing.wav", "timings.json"):
        target = path / name
        if target.exists():
            target.unlink()
