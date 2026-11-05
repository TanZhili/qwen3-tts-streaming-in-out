"""In-process stand-in that speaks the native /v1/ws frames.

This is not the TensorRT engine. It exists so the websocket client can be
exercised on a machine with no GPU: the first text frame is answered with
a binary PCM packet immediately, and each later text frame produces another
packet while the connection stays open.
"""

from __future__ import annotations

import json

from .pcm import SAMPLE_RATE, render_placeholder_pcm


async def start_loopback_server():
    import websockets

    async def handler(ws) -> None:
        sent = 0
        async for message in ws:
            if isinstance(message, bytes):
                continue
            body = json.loads(message)
            kind = str(body.get("type", ""))
            if kind == "start":
                continue
            if kind == "text":
                pcm = render_placeholder_pcm(sent)
                start = sent * (len(pcm) // 2)
                end = start + (len(pcm) // 2)
                header = {
                    "type": "audio_header",
                    "audio": {
                        "encoding": "pcm_s16le",
                        "sample_rate": SAMPLE_RATE,
                        "channels": 1,
                    },
                    "start_sample": start,
                    "end_sample": end,
                }
                await ws.send(json.dumps(header))
                await ws.send(pcm)
                sent += 1
                continue
            if kind in {"stop", "end"}:
                await ws.send(json.dumps({"type": "event", "event": {"type": "done"}}))
                break

    return await websockets.serve(handler, "127.0.0.1", 0)
