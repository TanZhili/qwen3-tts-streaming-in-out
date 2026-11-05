"""Hand LLM text tokens to TTS.

``stream_llm_text_tokens`` is the production hook. Each item is one text
token string exactly as the LLM tokenizer emitted it. The function does
not segment, does not look for spaces, and does not insert the demo's
240 ms grid.

``iter_demo_simulated_tokens`` exists only so this demo can run without a
live model. ``feed_tts(..., interval_ms=240)`` is the simulated LLM clock:
the first token is sent immediately, and each later token is held to a
240 ms grid. Pass ``interval_ms=0`` for a real token stream.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from .clock import precise_sleep_until
from .segment import approximate_demo_text_tokens
from .trace import Trace


class TextSink(Protocol):
    async def send_text(self, text: str) -> None: ...

    async def end_input(self) -> None: ...


async def iter_demo_simulated_tokens(text: str) -> AsyncIterator[str]:
    """Yield the demo-only character-pair approximation. No sleeping here."""

    for token in approximate_demo_text_tokens(text):
        yield token


async def stream_llm_text_tokens(
    session: TextSink,
    tokens: AsyncIterator[str],
    trace: Trace,
) -> None:
    """Send each LLM text token as soon as it is produced.

    ``tokens`` is the real integration point. Yield the tokenizer's text
    piece for every generated token and nothing else. Do not pre-segment
    the utterance into words, and do not assume the pieces contain spaces.
    """

    await feed_tts(session, tokens, trace, interval_ms=0)


async def feed_tts(
    session: TextSink,
    tokens: AsyncIterator[str],
    trace: Trace,
    interval_ms: float,
) -> None:
    """Forward each token string to TTS.

    ``interval_ms`` paces the *simulated* LLM only. The first token is
    sent on the calling turn. Later tokens wait until
    ``first_handoff + index * interval``. ``interval_ms == 0`` forwards
    each token as soon as the async iterator yields it.
    """

    interval_s = interval_ms / 1000.0
    origin: float | None = None
    index = 0
    async for token in tokens:
        if token is None or token == "":
            continue
        if origin is not None and interval_s > 0:
            await precise_sleep_until(origin + index * interval_s)
        if index == 0:
            trace.mark_llm_first(token)
        await session.send_text(token)
        if origin is None:
            if trace.first_text_pc is None:
                raise RuntimeError("TTS session did not record the first text handoff")
            origin = trace.first_text_pc
        index += 1
    if index == 0:
        raise RuntimeError("LLM stream produced no text tokens")
    await session.end_input()
