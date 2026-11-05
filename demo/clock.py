"""Shared clocks for the latency log.

Deltas use time.perf_counter. Wall-clock lines are ISO-8601 local time
and are not used to compute ttfa_ms.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime


class Clock:
    def __init__(self) -> None:
        self.mono0 = time.perf_counter()
        self.wall0 = datetime.now().astimezone()

    def mono_ms(self, pc: float | None = None) -> float:
        if pc is None:
            pc = time.perf_counter()
        return (pc - self.mono0) * 1000.0

    def wall_iso(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def origin_wall_iso(self) -> str:
        return self.wall0.isoformat(timespec="milliseconds")


async def precise_sleep_until(target: float) -> None:
    """Sleep until a perf_counter deadline.

    The last few milliseconds spin so the 240 ms grid is not limited to
    the Windows timer quantum. The spin is only the tail of the wait.
    """

    while True:
        remain = target - time.perf_counter()
        if remain <= 0:
            return
        if remain > 0.030:
            await asyncio.sleep(remain - 0.020)
        elif remain > 0.004:
            await asyncio.sleep(0)
        else:
            while time.perf_counter() < target:
                pass
            return
