"""Streaming text into Qwen3-TTS, measuring time to the first audio packet."""

from .segment import segment_text

__all__ = ["segment_text"]
