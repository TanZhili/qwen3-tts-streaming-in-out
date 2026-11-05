"""Streaming text-in / audio-out demo entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from pathlib import Path

from .clock import Clock
from .feed import feed_tts, iter_llm_deltas
from .local_backend import LocalStreamingSession
from .pcm import prepare_out_dir
from .segment import segment_text, self_check
from .trace import Trace
from .xsquare_backend import XSquareStreamingSession

DEFAULT_TEXT = (
    "你好，这是流式文本输入。第一包不等全文，后面的词会继续到达。"
    "后续音频可以看到更长的上下文。"
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream scripted LLM text into a Qwen3-TTS session and measure "
            "time to the first audio packet."
        )
    )
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text the scripted LLM will speak.")
    parser.add_argument(
        "--interval-ms",
        type=float,
        default=240.0,
        help="Milliseconds between TTS handoffs after the first unit. Default: 240. Use 0 to forward deltas immediately.",
    )
    parser.add_argument(
        "--backend",
        choices=("local", "loopback", "xsquare", "auto"),
        default="local",
        help=(
            "local: in-process WAIT_TEXT pipeline with placeholder PCM. "
            "loopback: same wire protocol as X-Square /v1/ws against an in-process server. "
            "xsquare: real engine at --url. "
            "auto: try xsquare, then local."
        ),
    )
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:50052/v1/ws",
        help="X-Square native websocket URL.",
    )
    parser.add_argument("--speaker", default="Serena")
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--task-type", default="custom_voice")
    parser.add_argument(
        "--decode-step-ms",
        type=float,
        default=200.0,
        help="Local backend only: spacing of packets after the first. Does not delay the first packet.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("output"),
        help="Directory for chunk WAVs, growing.wav, and timings.json.",
    )
    return parser.parse_args(argv)


async def _open_session(args, trace: Trace, out_dir: Path):
    backend = args.backend
    holder: dict = {"server": None, "name": backend}

    if backend == "auto":
        session = XSquareStreamingSession(
            trace,
            out_dir,
            args.url,
            speaker=args.speaker,
            language=args.language,
            task_type=args.task_type,
        )
        try:
            await session.start()
        except Exception as exc:
            print(
                f"xsquare_unavailable reason={exc!r} fallback=local",
                flush=True,
            )
            await session.aclose()
            backend = "local"
            holder["name"] = "local"
        else:
            holder["name"] = "xsquare"
            holder["session"] = session
            return holder

    if backend == "loopback":
        from .loopback import start_loopback_server

        server = await start_loopback_server()
        sockets = list(server.sockets or [])
        if not sockets:
            raise RuntimeError("loopback websocket server has no socket")
        port = sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}/v1/ws"
        session = XSquareStreamingSession(
            trace,
            out_dir,
            url,
            speaker=args.speaker,
            language=args.language,
            task_type=args.task_type,
        )
        await session.start()
        holder["server"] = server
        holder["session"] = session
        holder["name"] = "loopback"
        return holder

    if backend == "xsquare":
        session = XSquareStreamingSession(
            trace,
            out_dir,
            args.url,
            speaker=args.speaker,
            language=args.language,
            task_type=args.task_type,
        )
        await session.start()
        holder["session"] = session
        holder["name"] = "xsquare"
        return holder

    session = LocalStreamingSession(trace, out_dir, args.decode_step_ms / 1000.0)
    await session.start()
    holder["session"] = session
    holder["name"] = "local"
    return holder


def _audio_kind(name: str) -> str:
    if name == "xsquare":
        return "engine_pcm"
    if name == "loopback":
        return "loopback_pcm"
    return "placeholder_pcm"


def evaluate(trace: Trace, interval_ms: float) -> list[str]:
    errors: list[str] = []
    if trace.first_text_pc is None or trace.first_audio_pc is None or trace.ttfa_ms is None:
        return ["first text or first audio was not measured"]
    if trace.ttfa_ms < 0:
        errors.append(f"ttfa_ms is negative ({trace.ttfa_ms:.3f})")
    gaps = trace.word_gaps_ms()
    if interval_ms > 0:
        if not gaps:
            errors.append("need at least two text units to check the feed interval")
        for index, gap in enumerate(gaps, start=1):
            if abs(gap - interval_ms) > 15.0:
                errors.append(
                    f"word gap {index} is {gap:.3f} ms, expected {interval_ms:.3f} ± 15"
                )
        if len(trace.word_pcs) >= 2 and not (trace.first_audio_pc < trace.word_pcs[1]):
            errors.append("first audio packet was not ready before the second text unit")
    if not any(chunk.extra_text for chunk in trace.chunks):
        errors.append("no later audio chunk saw text beyond the first packet")
    return errors


def _check_wavs(trace: Trace, out_dir: Path) -> list[str]:
    errors: list[str] = []
    total = 0
    if not trace.chunks:
        return ["no wav chunks were written"]
    for chunk in trace.chunks:
        path = Path(chunk.wav_path)
        if not path.is_file():
            errors.append(f"missing {path}")
            continue
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                errors.append(f"{path.name} is not mono s16")
            if handle.getframerate() != 24000:
                errors.append(f"{path.name} sample rate is {handle.getframerate()}")
            if handle.getnframes() != chunk.samples:
                errors.append(f"{path.name} frame count does not match the log")
            total += handle.getnframes()
    growing = out_dir / "growing.wav"
    if not growing.is_file():
        errors.append("missing growing.wav")
    else:
        with wave.open(str(growing), "rb") as handle:
            if handle.getnframes() != total:
                errors.append(
                    f"growing.wav has {handle.getnframes()} frames, chunks sum to {total}"
                )
    return errors


async def async_main(args: argparse.Namespace) -> int:
    self_check()
    units = segment_text(args.text)
    if args.interval_ms < 0:
        print("interval-ms must be >= 0", file=sys.stderr)
        return 2

    clock = Clock()
    trace = Trace(clock)
    out_dir = args.out_dir
    prepare_out_dir(out_dir)
    print(f"clock_origin_wall={clock.origin_wall_iso()}", flush=True)
    print(
        f"feed_units={len(units)} interval_ms={args.interval_ms:.3f} "
        f"segmentation={'whitespace' if any(ch.isspace() for ch in args.text) else 'clause'}",
        flush=True,
    )

    holder = await _open_session(args, trace, out_dir)
    session = holder["session"]
    backend_name = holder["name"]
    audio_kind = _audio_kind(backend_name)
    print(
        f"backend={backend_name} audio_kind={audio_kind} interval_ms={args.interval_ms:.3f}",
        flush=True,
    )
    try:
        feeder = asyncio.create_task(
            feed_tts(session, iter_llm_deltas(args.text), trace, args.interval_ms),
            name="llm-feed",
        )
        async for _pcm in session.iter_audio():
            pass
        await feeder
    finally:
        closer = getattr(session, "aclose", None)
        if closer is not None:
            await closer()
        server = holder.get("server")
        if server is not None:
            server.close()
            await server.wait_closed()

    payload = trace.to_json(
        backend=backend_name,
        audio_kind=audio_kind,
        interval_ms=args.interval_ms,
    )
    (out_dir / "timings.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    errors = evaluate(trace, args.interval_ms) + _check_wavs(trace, out_dir)
    gaps = ",".join(f"{gap:.3f}" for gap in trace.word_gaps_ms())
    before_second = (
        len(trace.word_pcs) >= 2
        and trace.first_audio_pc is not None
        and trace.first_audio_pc < trace.word_pcs[1]
    )
    extra_chunks = sum(1 for chunk in trace.chunks if chunk.extra_text)
    ttfa_text = "none" if trace.ttfa_ms is None else f"{trace.ttfa_ms:.3f}"
    print(
        "summary "
        f"backend={backend_name} audio_kind={audio_kind} "
        f"interval_ms={args.interval_ms:.3f} "
        f"ttfa_ms={ttfa_text} "
        f"word_gaps_ms={gaps or 'none'} "
        f"first_audio_before_second_word={str(before_second).lower()} "
        f"later_extra_chunks={extra_chunks} "
        f"check={'pass' if not errors else 'fail'}",
        flush=True,
    )
    for error in errors:
        print(f"check_error {error}", flush=True)
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> None:
    _configure_stdio()
    args = parse_args(argv)
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
