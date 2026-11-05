"""Demo-only stand-in for an LLM text-token stream.

Production code must not call this. A real LLM already emits text tokens;
pass those strings through unchanged. See ``stream_llm_text_tokens`` in
``demo/feed.py``.

This machine has no ``tokenizers`` / ``transformers`` install and no
cached Qwen tokenizer, so the demo does not download one. It walks the
scripted string as characters and emits groups of at most two characters.
Whitespace is a character, never a delimiter, and it is never dropped.
"""

from __future__ import annotations

DEMO_TOKEN_SOURCE = "char_pairs_2"
DEMO_CHARS_PER_TOKEN = 2


def approximate_demo_text_tokens(text: str) -> list[str]:
    """Approximate one LLM text-token string per 1–2 characters.

    Leading and trailing padding on the scripted sentence is removed.
    Every remaining character, including spaces and punctuation, stays in
    the stream. Tokens are groups of ``DEMO_CHARS_PER_TOKEN`` characters,
    with a final shorter group when the length is odd.
    """

    raw = text.strip()
    if not raw:
        raise ValueError("demo text is empty")
    tokens: list[str] = []
    buf: list[str] = []
    for ch in raw:
        buf.append(ch)
        if len(buf) == DEMO_CHARS_PER_TOKEN:
            tokens.append("".join(buf))
            buf = []
    if buf:
        tokens.append("".join(buf))
    return tokens


def self_check() -> None:
    assert approximate_demo_text_tokens("你") == ["你"]
    assert approximate_demo_text_tokens("你好世界") == ["你好", "世界"]
    assert approximate_demo_text_tokens("你好世") == ["你好", "世"]
    assert approximate_demo_text_tokens("你好，世界。") == ["你好", "，世", "界。"]

    spaced = approximate_demo_text_tokens("你好 世界")
    assert "".join(spaced) == "你好 世界"
    assert spaced == ["你好", " 世", "界"]
    assert spaced != ["你好", "世界"]

    hello = approximate_demo_text_tokens("Hello world")
    assert "".join(hello) == "Hello world"
    assert hello != ["Hello", "world"]
    assert any(" " in token for token in hello)

    mixed = approximate_demo_text_tokens("你好 Hello")
    assert "".join(mixed) == "你好 Hello"
    assert mixed[0] == "你好"
    assert " " in mixed[1]
