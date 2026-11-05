"""Streaming text into Qwen3-TTS, measuring time to the first audio packet."""

from .feed import stream_llm_text_tokens
from .segment import approximate_demo_text_tokens

__all__ = ["approximate_demo_text_tokens", "stream_llm_text_tokens"]
