"""Incremental parser for the agents' JSON-streaming outputs.

The model emits one JSON object whose top-level fields we route by name:

- ``stream_string_fields`` — emit ``("<key>_chunk", str)`` for each character
  as the value streams in, and ``("<key>_done", None)`` when the closing
  quote arrives. Used for the user-visible streaming text in Phase 8
  (``"question"``) and Phase 9 (``"feedback"``, ``"model_answer"``).
- ``scalar_fields`` — emit ``("<key>", value)`` once when the value closes.
  Used for the integer score in Phase 9.

After the stream ends, the full buffer is parsed via ``json.loads`` and
yielded as ``("done", dict)`` — this is the authoritative source of truth
for downstream persistence (anti-drift: streamed text is byte-identical to
the persisted JSON).

Phase 8 originally hard-coded ``"question"``; Phase 9 generalised this so
the same parser drives both nodes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import Any


class StreamingJsonError(Exception):
    """Raised when the streamed buffer fails to parse at end-of-stream, or
    when the structure is recognizable as wrong before then (e.g. the first
    non-whitespace byte is not `{`)."""


# State machine states.
_PRE = "pre"  # before we've found the opening `{`
_OBJ = "obj"  # inside the object, between fields, looking for a key
_KEY = "key"  # reading a key string
_AFTER_KEY = "after_key"  # consumed key, looking for `:`
_BEFORE_VAL = "before_val"  # consumed `:`, looking for value start
_STRING_VAL = "string_val"  # inside a streamed string value — emit chars
_SCALAR_VAL = "scalar_val"  # inside a numeric/bool/null value — buffer
_SKIP_VAL = "skip_val"  # value of a key we don't care about; skip past it
_DONE_STREAMING = "done_streaming"  # all interesting fields finished


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


async def stream_json_object(
    deltas: AsyncIterator[str],
    *,
    stream_string_fields: Iterable[str] = (),
    scalar_fields: Iterable[str] = (),
) -> AsyncIterator[tuple[str, Any]]:
    """Drive the parser from an async iterator of model output chunks.

    Chunk boundaries are arbitrary — escape sequences (``\\"``, ``\\n``,
    ``\\u0041``) may split across chunks. The state machine works on
    individual characters.

    Yielded events:
        ``("<key>_chunk", str)`` — for each unescaped character of a
            ``stream_string_fields`` value as it arrives.
        ``("<key>_done", None)`` — when the streamed string value closes.
        ``("<key>", value)`` — once, for each ``scalar_fields`` value.
        ``("done", dict)`` — once, at end-of-stream, after ``json.loads``.

    Raises:
        StreamingJsonError: malformed JSON at end-of-stream, or first
            non-whitespace byte was not ``{``.
    """
    string_targets = set(stream_string_fields)
    scalar_targets = set(scalar_fields)

    buf: list[str] = []
    state = _PRE
    key_chars: list[str] = []
    active_key = ""  # name of the value currently being parsed
    scalar_buf: list[str] = []
    skip_depth = 0  # nesting depth while skipping a value we don't stream

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
                continue

            if state == _OBJ:
                if ch.isspace() or ch == ",":
                    continue
                if ch == "}":
                    state = _DONE_STREAMING
                    continue
                if ch == '"':
                    state = _KEY
                    key_chars = []
                    in_escape = False
                    continue
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
                    active_key = "".join(key_chars)
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
                continue

            if state == _BEFORE_VAL:
                if ch.isspace():
                    continue
                if active_key in string_targets:
                    if ch == '"':
                        state = _STRING_VAL
                        in_escape = False
                        unicode_pending = 0
                        unicode_acc = []
                        continue
                    # The model produced something other than a string for a
                    # key we expected to stream as a string. Skip the value
                    # and let the final json.loads decide whether it's valid
                    # (and which schema error to surface).
                    state = _SKIP_VAL
                    skip_depth = 0
                    # Re-enter SKIP_VAL handling for this char.
                elif active_key in scalar_targets:
                    state = _SCALAR_VAL
                    scalar_buf = [ch]
                    continue
                else:
                    state = _SKIP_VAL
                    skip_depth = 0
                    # Fall through into _SKIP_VAL handling for this char.

            if state == _SKIP_VAL:
                # Consume one full JSON value, then return to _OBJ.
                if skip_depth == 0:
                    if ch == '"':
                        # String value — track quote pairs (handle escapes).
                        skip_depth = -1  # sentinel for "inside a string"
                        in_escape = False
                        continue
                    if ch == "{" or ch == "[":
                        skip_depth = 1
                        continue
                    if ch == "," or ch == "}":
                        # End of a primitive value (number/true/false/null).
                        if ch == "}":
                            state = _DONE_STREAMING
                        else:
                            state = _OBJ
                        continue
                    # Still consuming primitive characters.
                    continue
                if skip_depth == -1:
                    # Inside a string value being skipped.
                    if in_escape:
                        in_escape = False
                        continue
                    if ch == "\\":
                        in_escape = True
                        continue
                    if ch == '"':
                        skip_depth = 0
                        # Need to consume the trailing `,` or `}` next.
                        # Move back to a "look for terminator" sub-state by
                        # falling through: but we've already consumed the
                        # closing quote, so just return to _OBJ on the next
                        # `,` / `}` we see.
                        state = _OBJ
                        continue
                    continue
                # Nested object/array.
                if ch == "{" or ch == "[":
                    skip_depth += 1
                elif ch == "}" or ch == "]":
                    skip_depth -= 1
                    if skip_depth == 0:
                        state = _OBJ
                continue

            if state == _SCALAR_VAL:
                if ch == "," or ch == "}" or ch.isspace():
                    raw = "".join(scalar_buf).strip()
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError as e:
                        raise StreamingJsonError(
                            f"could not parse scalar value for {active_key!r}: {raw!r}"
                        ) from e
                    yield (active_key, value)
                    if ch == "}":
                        state = _DONE_STREAMING
                    else:
                        state = _OBJ
                    continue
                scalar_buf.append(ch)
                continue

            if state == _STRING_VAL:
                if unicode_pending > 0:
                    unicode_acc.append(ch)
                    unicode_pending -= 1
                    if unicode_pending == 0:
                        try:
                            decoded = chr(int("".join(unicode_acc), 16))
                        except ValueError as e:
                            raise StreamingJsonError(
                                f"invalid \\u escape in {active_key}: {''.join(unicode_acc)!r}"
                            ) from e
                        yield (f"{active_key}_chunk", decoded)
                    continue
                if in_escape:
                    in_escape = False
                    if ch == "u":
                        unicode_pending = 4
                        unicode_acc = []
                        continue
                    yield (f"{active_key}_chunk", _unescape_simple(ch))
                    continue
                if ch == "\\":
                    in_escape = True
                    continue
                if ch == '"':
                    yield (f"{active_key}_done", None)
                    state = _OBJ
                    continue
                yield (f"{active_key}_chunk", ch)
                continue

    full = "".join(buf)
    try:
        parsed = json.loads(full)
    except json.JSONDecodeError as e:
        raise StreamingJsonError(f"malformed JSON at end-of-stream: {e}") from e

    if not isinstance(parsed, dict):
        raise StreamingJsonError(f"expected JSON object, got {type(parsed).__name__}")

    yield ("done", parsed)
