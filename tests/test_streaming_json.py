"""Tests for the incremental JSON parser used by QuestionGenerator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from interview_coach.agents.streaming_json import (
    StreamingJsonError,
    stream_json_object,
)


async def _feed(chunks: list[str]) -> AsyncIterator[str]:
    for c in chunks:
        yield c


async def _drive(chunks: list[str]) -> tuple[str, dict[str, Any] | None]:
    """Drive the parser as Phase 8 does: stream `question`, ignore anchors."""
    streamed = ""
    parsed: dict[str, Any] | None = None
    async for kind, data in stream_json_object(_feed(chunks), stream_string_fields=("question",)):
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


async def test_question_streams_even_if_anchors_came_first() -> None:
    """The parser dispatches by key, so `question` still streams even if the
    model misbehaves and emits anchors first. Prompts steer towards
    question-first to maximise TTFT, but correctness doesn't depend on it."""
    raw = '{"anchors": ["a", "b"], "question": "later"}'
    streamed, parsed = await _drive([raw])
    assert streamed == "later"
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


# --- Phase 9 multi-field variant ---


async def _drive_eval(chunks: list[str]) -> dict[str, Any]:
    """Drive the parser the way the Phase 9 evaluator does: one int scalar
    + two streamed string fields, then a final parsed dict.

    Returns a dict capturing each event class for assertion convenience.
    """
    score: int | None = None
    feedback = ""
    feedback_done = False
    model_answer = ""
    model_answer_done = False
    parsed: dict[str, Any] | None = None

    async for kind, data in stream_json_object(
        _feed(chunks),
        stream_string_fields=("feedback", "model_answer"),
        scalar_fields=("score",),
    ):
        if kind == "score":
            score = data
        elif kind == "feedback_chunk":
            feedback += data
        elif kind == "feedback_done":
            feedback_done = True
        elif kind == "model_answer_chunk":
            model_answer += data
        elif kind == "model_answer_done":
            model_answer_done = True
        elif kind == "done":
            parsed = data

    return {
        "score": score,
        "feedback": feedback,
        "feedback_done": feedback_done,
        "model_answer": model_answer,
        "model_answer_done": model_answer_done,
        "parsed": parsed,
    }


async def test_eval_score_arrives_before_feedback_chunks() -> None:
    """Common case: model emits score first."""
    raw = (
        '{"score": 7, "feedback": "Strong on tradeoffs.", '
        '"model_answer": "When I led the rewrite, I..."}'
    )
    out = await _drive_eval([raw])
    assert out["score"] == 7
    assert out["feedback"] == "Strong on tradeoffs."
    assert out["feedback_done"] is True
    assert out["model_answer"] == "When I led the rewrite, I..."
    assert out["model_answer_done"] is True
    assert out["parsed"] is not None
    assert out["parsed"]["score"] == 7


async def test_eval_chunked_streaming() -> None:
    """Arbitrary chunk boundaries don't corrupt any field."""
    full = (
        '{"score": 9, "feedback": "Comprehensive answer with specifics.", '
        '"model_answer": "I would design X by..."}'
    )
    chunks = [full[i : i + 4] for i in range(0, len(full), 4)]
    out = await _drive_eval(chunks)
    assert out["score"] == 9
    assert out["feedback"] == "Comprehensive answer with specifics."
    assert out["model_answer"] == "I would design X by..."


async def test_eval_score_split_across_chunks() -> None:
    """Multi-digit score split mid-number must parse correctly."""
    chunks = ['{"score": 1', '0, "feedback": "x", "model_answer": "y"}']
    out = await _drive_eval(chunks)
    assert out["score"] == 10
    assert out["feedback"] == "x"


async def test_eval_unicode_in_feedback() -> None:
    raw = '{"score": 5, "feedback": "café \\u263a", "model_answer": "x"}'
    out = await _drive_eval([raw])
    assert out["feedback"] == "café ☺"


async def test_eval_field_order_swapped() -> None:
    """If the model puts feedback before score, both still get captured."""
    raw = '{"feedback": "ok", "score": 6, "model_answer": "x"}'
    out = await _drive_eval([raw])
    assert out["score"] == 6
    assert out["feedback"] == "ok"
    assert out["model_answer"] == "x"


async def test_eval_extra_unrelated_field_skipped() -> None:
    """Unknown top-level keys with primitive / array / object values are skipped."""
    raw = (
        '{"score": 4, "extra_int": 99, "extra_str": "skip", '
        '"extra_arr": [1, 2, 3], "extra_obj": {"k": "v"}, '
        '"feedback": "f", "model_answer": "m"}'
    )
    out = await _drive_eval([raw])
    assert out["score"] == 4
    assert out["feedback"] == "f"
    assert out["model_answer"] == "m"
    assert out["parsed"]["extra_int"] == 99


async def test_eval_quote_in_feedback() -> None:
    raw = '{"score": 7, "feedback": "She said \\"go\\".", "model_answer": "x"}'
    out = await _drive_eval([raw])
    assert out["feedback"] == 'She said "go".'
