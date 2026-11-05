"""Print and store the latency trace.

first_text_in_ms / first_audio_out_ms are monotonic milliseconds from
Clock.mono0. ttfa_ms is first_audio perf_counter minus first_text
perf_counter, in milliseconds. Nothing here is estimated from a
published benchmark.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .clock import Clock


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


@dataclass
class ChunkRecord:
    index: int
    mono_ms: float
    wall: str
    nbytes: int
    samples: int
    visible_text: str
    extra_text: str
    wav_path: str


@dataclass
class Trace:
    clock: Clock
    buffer: str = ""
    llm_first_pc: float | None = None
    llm_first_wall: str = ""
    llm_first_text: str = ""
    first_text_pc: float | None = None
    first_text_wall: str = ""
    first_text: str = ""
    first_audio_pc: float | None = None
    first_audio_wall: str = ""
    first_audio_bytes: int = 0
    first_audio_samples: int = 0
    text_at_first_audio: str | None = None
    word_pcs: list[float] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    chunks: list[ChunkRecord] = field(default_factory=list)

    def mark_llm_first(self, text: str) -> None:
        if self.llm_first_pc is not None:
            return
        pc = time.perf_counter()
        wall = self.clock.wall_iso()
        self.llm_first_pc = pc
        self.llm_first_wall = wall
        self.llm_first_text = text
        print(
            f"llm_first_text_ms={self.clock.mono_ms(pc):.3f} "
            f"wall={wall} text={_q(text)}",
            flush=True,
        )

    def mark_text_in(self, text: str) -> float:
        """Record the moment this unit is handed to the TTS session.

        Callers must invoke this immediately before the real handoff
        (buffer append, or the websocket send of the text frame).
        """

        pc = time.perf_counter()
        wall = self.clock.wall_iso()
        gap_ms = 0.0 if not self.word_pcs else (pc - self.word_pcs[-1]) * 1000.0
        index = len(self.word_pcs)
        self.word_pcs.append(pc)
        self.words.append(text)
        self.buffer += text
        if self.first_text_pc is None:
            self.first_text_pc = pc
            self.first_text_wall = wall
            self.first_text = text
            print(
                f"first_text_in_ms={self.clock.mono_ms(pc):.3f} "
                f"wall={wall} text={_q(text)}",
                flush=True,
            )
        print(
            f"word_send index={index} gap_ms={gap_ms:.3f} "
            f"mono_ms={self.clock.mono_ms(pc):.3f} text={_q(text)}",
            flush=True,
        )
        return pc

    def mark_audio(self, pcm: bytes, sample_rate: int, visible: str, wav_path: str) -> ChunkRecord:
        """Record a PCM packet that already exists in memory."""

        pc = time.perf_counter()
        wall = self.clock.wall_iso()
        samples = len(pcm) // 2
        if self.text_at_first_audio is None:
            extra = ""
            self.text_at_first_audio = visible
        elif visible.startswith(self.text_at_first_audio):
            extra = visible[len(self.text_at_first_audio) :]
        else:
            extra = visible
        record = ChunkRecord(
            index=len(self.chunks),
            mono_ms=self.clock.mono_ms(pc),
            wall=wall,
            nbytes=len(pcm),
            samples=samples,
            visible_text=visible,
            extra_text=extra,
            wav_path=wav_path,
        )
        self.chunks.append(record)
        if self.first_audio_pc is None:
            if self.first_text_pc is None:
                raise RuntimeError("audio packet arrived before any text was handed to TTS")
            self.first_audio_pc = pc
            self.first_audio_wall = wall
            self.first_audio_bytes = len(pcm)
            self.first_audio_samples = samples
            ttfa_ms = (pc - self.first_text_pc) * 1000.0
            print(
                f"first_audio_out_ms={self.clock.mono_ms(pc):.3f} "
                f"wall={wall} bytes={len(pcm)} samples={samples}",
                flush=True,
            )
            print(f"ttfa_ms={ttfa_ms:.3f}", flush=True)
        if extra:
            print(
                f"later_context index={record.index} "
                f"extra_text={_q(extra)} visible_text={_q(visible)}",
                flush=True,
            )
        print(
            f"audio_chunk index={record.index} mono_ms={record.mono_ms:.3f} "
            f"bytes={record.nbytes} samples={record.samples} "
            f"extra_text={_q(extra)} visible_text={_q(visible)}",
            flush=True,
        )
        return record

    @property
    def ttfa_ms(self) -> float | None:
        if self.first_text_pc is None or self.first_audio_pc is None:
            return None
        return (self.first_audio_pc - self.first_text_pc) * 1000.0

    def word_gaps_ms(self) -> list[float]:
        return [
            (self.word_pcs[i] - self.word_pcs[i - 1]) * 1000.0
            for i in range(1, len(self.word_pcs))
        ]

    def to_json(self, *, backend: str, audio_kind: str, interval_ms: float) -> dict[str, Any]:
        return {
            "backend": backend,
            "audio_kind": audio_kind,
            "interval_ms": interval_ms,
            "clock_origin_wall": self.clock.origin_wall_iso(),
            "llm_first_text_ms": None
            if self.llm_first_pc is None
            else self.clock.mono_ms(self.llm_first_pc),
            "llm_first_text_wall": self.llm_first_wall,
            "first_text_in_ms": None
            if self.first_text_pc is None
            else self.clock.mono_ms(self.first_text_pc),
            "first_text_in_wall": self.first_text_wall,
            "first_text": self.first_text,
            "first_audio_out_ms": None
            if self.first_audio_pc is None
            else self.clock.mono_ms(self.first_audio_pc),
            "first_audio_out_wall": self.first_audio_wall,
            "first_audio_bytes": self.first_audio_bytes,
            "first_audio_samples": self.first_audio_samples,
            "ttfa_ms": self.ttfa_ms,
            "text_at_first_audio": self.text_at_first_audio,
            "words": [
                {
                    "index": i,
                    "text": word,
                    "mono_ms": self.clock.mono_ms(pc),
                    "gap_ms": 0.0 if i == 0 else (pc - self.word_pcs[i - 1]) * 1000.0,
                }
                for i, (word, pc) in enumerate(zip(self.words, self.word_pcs))
            ],
            "chunks": [
                {
                    "index": chunk.index,
                    "mono_ms": chunk.mono_ms,
                    "wall": chunk.wall,
                    "bytes": chunk.nbytes,
                    "samples": chunk.samples,
                    "visible_text": chunk.visible_text,
                    "extra_text": chunk.extra_text,
                    "wav_path": chunk.wav_path,
                }
                for chunk in self.chunks
            ],
        }
