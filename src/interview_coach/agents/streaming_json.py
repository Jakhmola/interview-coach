"""Incremental parser for the QuestionGenerator's JSON-streaming output.

The model emits one JSON object whose first field is `"question": "..."`,
followed by `"anchors": [...]`. We forward characters of the `question`
string value to the SSE client as they arrive, then drain the rest silently
and validate the full buffer at end-of-stream.

Yielded events:
- ``("question_chunk", str)`` — a piece of the unescaped `question` value.
- ``("done", dict[str, Any])`` — the fully parsed JSON object, exactly once,
  at end-of-stream.

Why hand-roll this instead of using a library: the only thing we need is to
stream the value of one specific top-level string field. A 60-line state
machine is simpler and dependency-free.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


class StreamingJsonError(Exception):
    """Raised when the streamed buffer fails to parse at end-of-stream, or
    when the structure is recognizable as wrong before then (e.g. the first
    non-whitespace byte is not `{`)."""


# State machine: we walk the prefix of the JSON until we've finished
# emitting the `question` value, then we stop scanning and just buffer.
# (Full validation happens once via json.loads at end-of-stream.)
_PRE = "pre"  # before we've found the opening `{`
_OBJ = "obj"  # inside the object, between fields, looking for a key
_KEY = "key"  # reading a key string
_AFTER_KEY = "after_key"  # consumed key, looking for `:`
_BEFORE_VAL = "before_val"  # consumed `:`, looking for value start
_QUESTION_VAL = "qval"  # inside the question string value — emit chars
_DONE_STREAMING = "done_streaming"  # question value closed; just buffer


async def stream_question_json(
    deltas: AsyncIterator[str],
) -> AsyncIterator[tuple[str, Any]]:
    """Drive the parser from an async iterator of model output chunks.

    Chunk boundaries are arbitrary — the model may split a `\\u0041` escape
    or a `\\"` across two deltas. The state machine only cares about
    individual characters.

    Raises:
        StreamingJsonError: malformed JSON at end-of-stream, or the first
            non-whitespace byte was not `{`.
    """
    buf: list[str] = []
    state = _PRE
    key_chars: list[str] = []
    target_key_seen = False  # True once we've identified the active key as `question`

    # Escape handling for a JSON string: when we see `\`, the next character
    # is an escape body. `\u` consumes 4 hex digits.
    in_escape = False
    unicode_pending = 0
    unicode_acc: list[str] = []

    async for chunk in deltas:
        buf.append(chunk)
        for ch in chunk:
            if state == _PRE:
                if ch.isspace():
                    continue
                if ch != "{":
                    raise StreamingJsonError(
                        f"expected JSON object, first non-space char was {ch!r}"
                    )
                state = _OBJ
                continue

            if state == _DONE_STREAMING:
                # Question value closed — just buffer until end-of-stream.
                continue

            if state == _OBJ:
                if ch.isspace() or ch == ",":
                    continue
                if ch == "}":
                    # Object closed before we found `question` value (or after
                    # we've finished it, which is _DONE_STREAMING — handled above).
                    state = _DONE_STREAMING
                    continue
                if ch == '"':
                    state = _KEY
                    key_chars = []
                    in_escape = False
                    continue
                # Other chars in object position are unexpected but we're not
                # validating here — let the final json.loads catch it.
                continue

            if state == _KEY:
                if in_escape:
                    key_chars.append(ch)
                    in_escape = False
                    continue
                if ch == "\\":
                    in_escape = True
                    continue
                if ch == '"':
                    key = "".join(key_chars)
                    target_key_seen = key == "question"
                    state = _AFTER_KEY
                    continue
                key_chars.append(ch)
                continue

            if state == _AFTER_KEY:
                if ch.isspace():
                    continue
                if ch == ":":
                    state = _BEFORE_VAL
                    continue
                # Malformed but defer to final parse.
                continue

            if state == _BEFORE_VAL:
                if ch.isspace():
                    continue
                if ch == '"' and target_key_seen:
                    state = _QUESTION_VAL
                    in_escape = False
                    unicode_pending = 0
                    unicode_acc = []
                    continue
                # We don't care about non-question values; transition to a
                # buffering mode that just waits for end-of-stream.
                state = _DONE_STREAMING
                continue

            if state == _QUESTION_VAL:
                if unicode_pending > 0:
                    unicode_acc.append(ch)
                    unicode_pending -= 1
                    if unicode_pending == 0:
                        try:
                            decoded = chr(int("".join(unicode_acc), 16))
                        except ValueError as e:
                            raise StreamingJsonError(
                                f"invalid \\u escape in question: {''.join(unicode_acc)!r}"
                            ) from e
                        yield ("question_chunk", decoded)
                    continue
                if in_escape:
                    in_escape = False
                    if ch == "u":
                        unicode_pending = 4
                        unicode_acc = []
                        continue
                    yield ("question_chunk", _unescape_simple(ch))
                    continue
                if ch == "\\":
                    in_escape = True
                    continue
                if ch == '"':
                    state = _DONE_STREAMING
                    continue
                yield ("question_chunk", ch)
                continue

    # End of stream — validate the whole buffer.
    full = "".join(buf)
    try:
        parsed = json.loads(full)
    except json.JSONDecodeError as e:
        raise StreamingJsonError(f"malformed JSON at end-of-stream: {e}") from e

    if not isinstance(parsed, dict):
        raise StreamingJsonError(f"expected JSON object, got {type(parsed).__name__}")

    yield ("done", parsed)


_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


def _unescape_simple(ch: str) -> str:
    """Resolve a single-char JSON escape body (everything except `\\uXXXX`)."""
    return _SIMPLE_ESCAPES.get(ch, ch)
