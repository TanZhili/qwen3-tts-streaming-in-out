"""In-process streaming session with the same text-in contract as the engine.

Text is appended to one buffer. The first PCM packet is built as soon as
the first unit is appended. Later packets read whatever the buffer holds
at that moment, so text that arrived after the first packet is visible.
When the buffer has been fully "covered" and the caller has not closed
the input, the loop waits (the engine's WAIT_TEXT) instead of freezing
the first fragment into a separate request.

The waveform is a placeholder tone. It proves the packet exists. It is
not Qwen3-TTS speech and its ttfa_ms is not a model benchmark.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .pcm import SAMPLE_RATE, render_placeholder_pcm, write_wav
from .trace import Trace


class LocalStreamingSession:
    audio_kind = "placeholder_pcm"

    def __init__(self, trace: Trace, out_dir: Path, decode_step_s: float) -> None:
        self.trace = trace
        self.out_dir = out_dir
        self.decode_step_s = decode_step_s
        self.sample_rate = SAMPLE_RATE
        self._cond = asyncio.Condition()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False
        self._task: asyncio.Task[None] | None = None
        self._pcm_all = bytearray()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._decode_loop(), name="local-tts-decode")

    async def send_text(self, text: str) -> None:
        async with self._cond:
            # Stamp and append before releasing the loop. The stamp is the
            # handoff into this TTS session, before any later-word wait.
            self.trace.mark_text_in(text)
            self._cond.notify_all()

    async def end_input(self) -> None:
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    async def iter_audio(self):
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
        if self._task is not None:
            await self._task

    async def aclose(self) -> None:
        await self.end_input()
        if self._task is not None:
            await self._task

    def _emit(self, visible: str) -> bytes:
        pcm = render_placeholder_pcm(len(self.trace.chunks))
        index = len(self.trace.chunks)
        chunk_path = self.out_dir / f"chunk_{index:03d}.wav"
        write_wav(chunk_path, pcm, self.sample_rate)
        self._pcm_all.extend(pcm)
        write_wav(self.out_dir / "growing.wav", bytes(self._pcm_all), self.sample_rate)
        self.trace.mark_audio(pcm, self.sample_rate, visible, str(chunk_path))
        return pcm

    async def _decode_loop(self) -> None:
        async with self._cond:
            while not self.trace.buffer and not self._closed:
                await self._cond.wait()
            visible = self.trace.buffer
            closed = self._closed
        if visible:
            pcm = self._emit(visible)
            await self._queue.put(pcm)
        if closed or not visible:
            await self._queue.put(None)
            return

        while True:
            await asyncio.sleep(self.decode_step_s)
            async with self._cond:
                visible = self.trace.buffer
                closed = self._closed
            pcm = self._emit(visible)
            await self._queue.put(pcm)
            if closed:
                break
        await self._queue.put(None)
