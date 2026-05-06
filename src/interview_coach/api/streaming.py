"""Tiny SSE helper. The wire format is line-oriented:

    event: <name>\\n
    data: <utf-8 payload>\\n
    \\n

Multi-line `data` is supported by repeating the `data:` prefix on each
line; we keep payloads single-line by JSON-encoding any non-string data.
"""

from __future__ import annotations

import json
from typing import Any


def sse_event(event: str, data: Any) -> bytes:
    """Format one SSE event. Strings are sent verbatim; dicts/lists are JSON.

    SSE forbids embedded newlines in `data:` without re-prefixing — we
    JSON-encode strings (`json.dumps(...)`) so newlines in question text
    become `\\n` escapes and the wire stays well-formed.
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode()


SSE_HEADERS = {
    # Disable buffering on intermediate proxies (uvicorn doesn't buffer SSE
    # itself, but nginx and friends do unless told otherwise).
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
