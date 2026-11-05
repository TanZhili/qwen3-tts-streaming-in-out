"""Split demo text into the units that are fed to TTS once per interval."""

from __future__ import annotations

import re

# Clause boundaries. The character stays with the preceding unit so the
# spoken fragment still ends on its punctuation.
_PUNCT = set("，。！？、；：,.!?;:…\n")
_WS = re.compile(r"\s+")


def segment_text(text: str) -> list[str]:
    """Return ordered feed units.

    Rule, applied in this order:

    1. If the string contains any whitespace, each whitespace-separated
       token is one unit (English words, or a bilingual line you already
       separated with spaces).
    2. Otherwise split on punctuation into short clauses. The punctuation
       character stays attached to the clause it closes.
    3. If that produces a single unit with no punctuation (a Chinese
       string with no spaces and no punctuation), group it into units of
       two characters. A trailing leftover character is its own unit.
    """

    raw = text.strip()
    if not raw:
        raise ValueError("demo text is empty")
    if re.search(r"\s", raw):
        return [part for part in _WS.split(raw) if part]

    units: list[str] = []
    buf: list[str] = []
    for ch in raw:
        buf.append(ch)
        if ch in _PUNCT:
            units.append("".join(buf))
            buf = []
    if buf:
        units.append("".join(buf))

    if len(units) == 1 and not any(ch in _PUNCT for ch in units[0]):
        return _groups_of_two(units[0])
    return units


def _groups_of_two(text: str) -> list[str]:
    units: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if len(buf) == 2:
            units.append("".join(buf))
            buf = []
    if buf:
        units.append("".join(buf))
    return units


def self_check() -> None:
    assert segment_text("你好，世界。") == ["你好，", "世界。"]
    assert segment_text("Hello world") == ["Hello", "world"]
    assert segment_text("  A   B  ") == ["A", "B"]
    assert segment_text("你好世界") == ["你好", "世界"]
    assert segment_text("你好世") == ["你好", "世"]
    assert segment_text("你") == ["你"]
