"""Scripted LLM deltas and the paced handoff into a TTS session.

`iter_llm_deltas` is the integration point. Replace it with a real model
stream that yields text as soon as the model produces it. The shipped
body only slices a fixed string; it does not sleep.

`feed_tts` is what the demo runs. With interval_ms > 0 it holds every
unit after the first so that TTS handoffs land on a 240 ms grid measured
with time.perf_counter. With interval_ms == 0 each delta is forwarded as
soon as the generator yields it.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from .clock import precise_sleep_until
from .segment import segment_text
from .trace import Trace


class TextSink(Protocol):
    async def send_text(self, text: str) -> None: ...

    async def end_input(self) -> None: ...


async def iter_llm_deltas(text: str) -> AsyncIterator[str]:
    """Yield text deltas for the demo.

    Replace this generator to plug in a real LLM, for example::

        async for event in client.chat.completions.create(..., stream=True):
            delta = event.choices[0].delta.content
            if delta:
                yield delta

    Then run with ``--interval-ms 0`` so the harness does not hold those
    deltas to the scripted 240 ms grid.
    """

    for unit in segment_text(text):
        yield unit


async def feed_tts(
    session: TextSink,
    deltas: AsyncIterator[str],
    trace: Trace,
    interval_ms: float,
) -> None:
    """Hand each delta to TTS. Pace from the first handoff when interval_ms > 0."""

    interval_s = interval_ms / 1000.0
    origin: float | None = None
    index = 0
    async for unit in deltas:
        if origin is not None and interval_s > 0:
            await precise_sleep_until(origin + index * interval_s)
        if index == 0:
            trace.mark_llm_first(unit)
        await session.send_text(unit)
        if origin is None:
            if trace.first_text_pc is None:
                raise RuntimeError("TTS session did not record the first text handoff")
            origin = trace.first_text_pc
        index += 1
    if index == 0:
        raise RuntimeError("LLM stream produced no text")
    await session.end_input()
