"""Client for X-Square-Robot/Qwen3TTS-Streaming native /v1/ws.

Wire format follows the engine SDK:

* {"type":"start","session_id","config":{task_type,language,speaker,audio}}
* {"type":"text","text":"..."} for each incremental unit
* {"type":"stop"} to finish input and drain audio
* a JSON audio_header followed by one binary PCM frame per packet
* {"type":"event","event":{"type":"done"|"error", ...}} ends the session

first_text_in is stamped immediately before the first text frame is sent.
first_audio_out is stamped when the first binary PCM frame is received.
ttfa_ms is the perf_counter delta of those two events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from .pcm import SAMPLE_RATE, write_wav
from .trace import Trace


class XSquareStreamingSession:
    audio_kind = "engine_pcm"

    def __init__(
        self,
        trace: Trace,
        out_dir: Path,
        url: str,
        *,
        speaker: str,
        language: str,
        task_type: str,
    ) -> None:
        self.trace = trace
        self.out_dir = out_dir
        self.url = url
        self.speaker = speaker
        self.language = language
        self.task_type = task_type
        self.sample_rate = SAMPLE_RATE
        self.encoding = "pcm_s16le"
        self.session_id = f"demo-{uuid.uuid4().hex[:8]}"
        self._ws = None
        self._recv_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._pcm_all = bytearray()

    async def start(self) -> None:
        import websockets

        self._ws = await websockets.connect(self.url, open_timeout=5, close_timeout=2)
        payload = {
            "type": "start",
            "session_id": self.session_id,
            "config": {
                "task_type": self.task_type,
                "language": self.language,
                "speaker": self.speaker,
                "audio": {
                    "encoding": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                    "channels": 1,
                },
            },
        }
        await self._ws.send(json.dumps(payload, ensure_ascii=False))
        self._recv_task = asyncio.create_task(self._recv_loop(), name="xsquare-recv")

    async def send_text(self, text: str) -> None:
        # Stamp and grow the client buffer before the socket write, so a
        # PCM frame that arrives while send is awaited already includes
        # this unit in visible_text.
        self.trace.mark_text_in(text)
        if self._ws is None:
            raise RuntimeError("session is not started")
        await self._ws.send(json.dumps({"type": "text", "text": text}, ensure_ascii=False))

    async def end_input(self) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "stop"}))

    async def iter_audio(self):
        while True:
            item = await self._queue.get()
            if item is None:
                break
            yield item
        if self._recv_task is not None:
            await self._recv_task

    async def aclose(self) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    self._on_pcm(message)
                    continue
                body = json.loads(message)
                kind = str(body.get("type", ""))
                if kind == "audio_header":
                    self._apply_audio_format(body.get("audio") or {})
                    continue
                if kind == "event":
                    event = body.get("event") or {}
                    if isinstance(event, dict) and isinstance(event.get("audio"), dict):
                        self._apply_audio_format(event["audio"])
                    event_type = str(event.get("type", "")) if isinstance(event, dict) else ""
                    if event_type in {"done", "error"}:
                        if event_type == "error":
                            _print_engine_error(str(event.get("message", "")))
                        break
                    continue
                if kind in {"done", "error"}:
                    if kind == "error":
                        _print_engine_error(str(body.get("message", "")))
                    break
        finally:
            await self._queue.put(None)

    def _apply_audio_format(self, audio: dict) -> None:
        if audio.get("sample_rate"):
            self.sample_rate = int(audio["sample_rate"])
        if audio.get("encoding"):
            self.encoding = str(audio["encoding"])

    def _on_pcm(self, payload: bytes) -> None:
        pcm = as_s16le(payload, self.encoding)
        index = len(self.trace.chunks)
        chunk_path = self.out_dir / f"chunk_{index:03d}.wav"
        # Stamp when the packet is in hand, before the WAV is written.
        self.trace.mark_audio(pcm, self.sample_rate, self.trace.buffer, str(chunk_path))
        write_wav(chunk_path, pcm, self.sample_rate)
        self._pcm_all.extend(pcm)
        write_wav(self.out_dir / "growing.wav", bytes(self._pcm_all), self.sample_rate)
        self._queue.put_nowait(pcm)


def as_s16le(payload: bytes, encoding: str) -> bytes:
    enc = encoding.lower()
    if enc in {"pcm_s16le", "pcm_s16", "s16le"}:
        even = len(payload) - (len(payload) % 2)
        return payload[:even]
    if enc in {"pcm_f32", "f32", "float32"}:
        import array

        usable = len(payload) - (len(payload) % 4)
        floats = array.array("f")
        floats.frombytes(payload[:usable])
        samples = array.array("h")
        for value in floats:
            clamped = max(-1.0, min(1.0, float(value)))
            samples.append(int(clamped * 32767.0))
        return samples.tobytes()
    raise RuntimeError(f"unsupported engine PCM encoding: {encoding}")


def _print_engine_error(message: str) -> None:
    print(
        f"engine_error message={json.dumps(message, ensure_ascii=False)}",
        flush=True,
    )
