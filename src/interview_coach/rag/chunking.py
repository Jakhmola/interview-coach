"""Section-aware token-window chunker.

Phase 14.1: split first on markdown headers (and fall back to triple-newline
soft breaks), then run a 400-token window with 50-token overlap per section.
Each chunk's text is prefixed with `[Project: <title>]\n[Section: <header>]\n`
when a project_title is supplied — keeps which-project context in-band so
both the embedding and human inspection benefit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    chunk_index: int
    text: str
    n_tokens: int


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

CODE_FENCE_PREFIX = "[CODE-FILE] "
"""Sentinel marking the start of an atomic code-file section.

``\\ue000`` (Unicode Private-Use-Area) never appears in real source or markdown,
so it can't collide, and — unlike the ASCII control chars ``\\x1c``–``\\x1f``,
which Python's ``str.strip()`` *does* treat as whitespace and would silently eat
— it survives ``.strip()``. A line ``\\ue000[CODE-FILE] <path>`` marks the
boundary of one source file in a grounding blob; the whole file body becomes a
*single* section (no internal markdown ``#``-splitting), which keeps code
coherent instead of shredding it at every ``# comment`` line. ``\\x00`` is
deliberately avoided (NUL bytes have corrupted plan files before). The sentinel
is parsed out in ``split_into_sections`` and never reaches the tokenizer or the
stored chunk text.
"""

_FENCE_RE = re.compile(r"^\[CODE-FILE\] (.+)$", re.MULTILINE)


def split_into_sections(text: str) -> list[tuple[str | None, str, bool]]:
    """Return `[(header, body, is_code), ...]`.

    Code files are fenced with ``CODE_FENCE_PREFIX + path`` lines. A blob looks
    like ``[prose extract_text][code file][code file]…``, so:
      - everything before the first fence is **prose**, split as in the
        prose branch below (each ``is_code=False``);
      - each fence-delimited block is **one atomic section**
        ``(path, body, True)`` with no internal markdown splitting.

    Prose strategy (no fence present → whole text is prose, back-compat):
      1. If the text contains any markdown headers (`# foo`, `## bar`, ...),
         split on those — header line goes into ``header``, the prose between
         headers into ``body``. Content before the first header keeps
         ``header=None``.
      2. Else, if the text has any ``\\n\\n\\n+`` runs, split there. All
         sections get ``header=None``.
      3. Else, one section with ``header=None``.

    Empty/whitespace-only bodies are filtered out.
    """
    if not text.strip():
        return []

    fences = list(_FENCE_RE.finditer(text))
    if fences:
        sections: list[tuple[str | None, str, bool]] = []
        prose = text[: fences[0].start()].strip()
        if prose:
            sections.extend(_split_prose(prose))
        for i, m in enumerate(fences):
            path = m.group(1).strip()
            body_start = m.end()
            body_end = fences[i + 1].start() if i + 1 < len(fences) else len(text)
            body = text[body_start:body_end].strip()
            if body:
                sections.append((path, body, True))
        return sections or _split_prose(text.strip())

    return _split_prose(text)


def _split_prose(text: str) -> list[tuple[str | None, str, bool]]:
    """Markdown-header / soft-break prose splitter (all ``is_code=False``)."""
    matches = list(_HEADER_RE.finditer(text))
    if matches:
        sections: list[tuple[str | None, str, bool]] = []
        pre = text[: matches[0].start()].strip()
        if pre:
            sections.append((None, pre, False))
        for i, m in enumerate(matches):
            header = m.group(2).strip()
            body_start = m.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            if body:
                sections.append((header, body, False))
        return sections or [(None, text.strip(), False)]

    parts = re.split(r"\n{3,}", text)
    sections = [(None, p.strip(), False) for p in parts if p.strip()]
    return sections or [(None, text.strip(), False)]


def _build_prefix(project_title: str | None, section_header: str | None) -> str:
    lines: list[str] = []
    if project_title:
        lines.append(f"[Project: {project_title}]")
    if section_header:
        lines.append(f"[Section: {section_header}]")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def chunk_text(
    text: str,
    *,
    tokenizer,  # transformers.PreTrainedTokenizerBase  # noqa: ANN001
    window: int = 400,
    overlap: int = 50,
    code_window: int = 600,
    code_overlap: int = 80,
    project_title: str | None = None,
) -> list[TextChunk]:
    """Split `text` into overlapping token-windows per section.

    Prose sections use ``window``/``overlap``; atomic code-file sections (fenced
    with ``CODE_FENCE_PREFIX``) use the larger ``code_window``/``code_overlap``
    so a source file stays coherent instead of shredding at every ``# comment``.

    When `project_title` is provided, every chunk's stored text is prefixed
    with `[Project: <title>]` plus the section header line. The prefix tokens
    count against the window size — we trim the body window accordingly so
    the final stored chunk is still ~`window` tokens.

    Empty / whitespace-only input yields an empty list.
    """
    text = text.strip()
    if not text:
        return []
    if window <= 0 or code_window <= 0:
        raise ValueError("window must be > 0")
    if overlap < 0 or overlap >= window:
        raise ValueError("overlap must be in [0, window)")
    if code_overlap < 0 or code_overlap >= code_window:
        raise ValueError("code_overlap must be in [0, code_window)")

    sections = split_into_sections(text)
    if not sections:
        return []

    chunks: list[TextChunk] = []
    idx = 0

    for header, body, is_code in sections:
        sec_window = code_window if is_code else window
        sec_overlap = code_overlap if is_code else overlap
        prefix = _build_prefix(project_title, header)
        prefix_token_count = (
            len(tokenizer(prefix, add_special_tokens=False)["input_ids"]) if prefix else 0
        )
        body_window = max(1, sec_window - prefix_token_count)
        body_step = max(1, body_window - sec_overlap) if body_window > sec_overlap else body_window

        enc = tokenizer(body, add_special_tokens=False, return_tensors=None)
        ids: list[int] = list(enc["input_ids"])
        if not ids:
            continue

        start = 0
        while start < len(ids):
            end = min(start + body_window, len(ids))
            slice_ids = ids[start:end]
            body_text = tokenizer.decode(slice_ids, skip_special_tokens=True).strip()
            if body_text:
                chunk_str = prefix + body_text
                chunks.append(
                    TextChunk(
                        chunk_index=idx,
                        text=chunk_str,
                        n_tokens=len(slice_ids) + prefix_token_count,
                    )
                )
                idx += 1
            if end == len(ids):
                break
            start += body_step

    return chunks
