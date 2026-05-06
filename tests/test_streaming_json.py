"""Tests for the incremental JSON parser used by QuestionGenerator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_question_json,
)


async def _feed(chunks: list[str]) -> AsyncIterator[str]:
    for c in chunks:
        yield c


async def _drive(chunks: list[str]) -> tuple[str, dict[str, Any] | None]:
    streamed = ""
    parsed: dict[str, Any] | None = None
    async for kind, data in stream_question_json(_feed(chunks)):
        if kind == "question_chunk":
            streamed += data
        elif kind == "done":
            parsed = data
    return streamed, parsed


async def test_simple_object() -> None:
    streamed, parsed = await _drive(['{"question": "Tell me about X", "anchors": ["a", "b"]}'])
    assert streamed == "Tell me about X"
    assert parsed == {"question": "Tell me about X", "anchors": ["a", "b"]}


async def test_chunked_arbitrary_boundaries() -> None:
    full = (
        '{"question": "Tell me about your async refactor", "anchors": ["latency", "error budget"]}'
    )
    chunks = [full[i : i + 3] for i in range(0, len(full), 3)]
    streamed, parsed = await _drive(chunks)
    assert streamed == "Tell me about your async refactor"
    assert parsed["anchors"] == ["latency", "error budget"]


async def test_question_string_escapes() -> None:
    raw = '{"question": "He said \\"hi\\"\\nthen left", "anchors": ["a"]}'
    streamed, parsed = await _drive([raw])
    assert streamed == 'He said "hi"\nthen left'
    assert parsed["question"] == 'He said "hi"\nthen left'


async def test_unicode_escape() -> None:
    raw = '{"question": "smile \\u263a yay", "anchors": ["a"]}'
    streamed, parsed = await _drive([raw])
    assert streamed == "smile ☺ yay"
    assert parsed["question"] == "smile ☺ yay"


async def test_split_mid_unicode_escape() -> None:
    """Chunk boundary inside a \\uXXXX sequence must not corrupt the output."""
    raw = '{"question": "x \\u263a y", "anchors": ["a"]}'
    # Split right between `\u26` and `3a`.
    cut = raw.index("3a")
    chunks = [raw[:cut], raw[cut:]]
    streamed, parsed = await _drive(chunks)
    assert streamed == "x ☺ y"
    assert parsed is not None


async def test_split_mid_simple_escape() -> None:
    """Chunk boundary between `\\` and `n`."""
    raw = '{"question": "a\\nb", "anchors": ["a"]}'
    cut = raw.index("n")
    chunks = [raw[:cut], raw[cut:]]
    streamed, parsed = await _drive(chunks)
    assert streamed == "a\nb"
    assert parsed is not None


async def test_anchors_first_is_not_streamed() -> None:
    """If the model emits anchors before question, we don't stream from it.
    The question value still flows through normally when its turn comes."""
    raw = '{"anchors": ["a", "b"], "question": "later"}'
    streamed, parsed = await _drive([raw])
    # The parser stops scanning once it sees `before_val` for a non-target key,
    # so nothing further is streamed even when `question` shows up.
    assert streamed == ""
    # But the full buffer still parses at end-of-stream.
    assert parsed == {"anchors": ["a", "b"], "question": "later"}


async def test_malformed_json_raises() -> None:
    with pytest.raises(StreamingJsonError):
        await _drive(['{"question": "x", "anchors": [missing closing'])


async def test_first_byte_not_brace_raises() -> None:
    with pytest.raises(StreamingJsonError):
        await _drive(["not json at all"])


async def test_leading_whitespace_ok() -> None:
    streamed, parsed = await _drive(['  \n  {"question": "ok", "anchors": []}'])
    assert streamed == "ok"
    assert parsed == {"question": "ok", "anchors": []}


async def test_top_level_array_raises() -> None:
    with pytest.raises(StreamingJsonError):
        await _drive(['["question", "anchors"]'])
