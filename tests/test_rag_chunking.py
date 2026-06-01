"""Unit tests for the section-aware chunker (Phase 32 Follow-up 4).

Follow-up 4 added an atomic code-file section: a source file fenced with
``CODE_FENCE_PREFIX`` is kept as ONE section (no internal ``#``-splitting) and
windowed at the larger ``code_window``, so code stops getting shredded at every
``# comment`` line. Fence-free blobs (CV / project_doc) must chunk exactly as
before.

A tiny whitespace tokenizer stands in for the real HF tokenizer — the chunker
only needs ``tokenizer(text)["input_ids"]`` (sliceable, countable) and
``decode``.
"""

from __future__ import annotations

from interview_coach.rag.chunking import (
    CODE_FENCE_PREFIX,
    chunk_text,
    split_into_sections,
)


class _WordTokenizer:
    """Whitespace tokenizer: one token per word, decode rejoins with spaces."""

    def __call__(self, text, add_special_tokens=False, return_tensors=None):  # noqa: ANN001
        return {"input_ids": text.split()}

    def decode(self, ids, skip_special_tokens=True):  # noqa: ANN001
        return " ".join(ids)


def test_code_file_is_one_section_not_one_per_comment() -> None:
    """A fenced code body full of ``# comment`` lines → exactly ONE code
    section (the bug was one section per comment line)."""
    code = "\n".join(
        [
            "import os  # stdlib",
            "# a comment",
            "def f():  # another",
            "    # indented comment",
            "    return 1  # trailing",
            "# closing comment",
        ]
    )
    blob = "# Repository: foo/bar\nA prose intro.\n\n" + CODE_FENCE_PREFIX + "src/app.py\n" + code

    sections = split_into_sections(blob)
    code_sections = [s for s in sections if s[2]]  # is_code
    assert len(code_sections) == 1, sections
    header, body, is_code = code_sections[0]
    assert header == "src/app.py"
    assert is_code is True
    # The whole file survives as one body — every comment line is still there.
    assert "# a comment" in body
    assert "# closing comment" in body
    # The fence sentinel itself never leaks into the stored body.
    assert "\x1f" not in body and "[CODE-FILE]" not in body


def test_prose_before_first_fence_still_markdown_split() -> None:
    """Prose ahead of the first fence keeps its markdown sectioning."""
    blob = (
        "# Description\nThe gateway.\n\n"
        "# Manifest: pyproject.toml\n[project]\nname='x'\n\n"
        + CODE_FENCE_PREFIX
        + "main.py\nprint('hi')  # go\n"
    )
    sections = split_into_sections(blob)
    headers = [h for h, _, _ in sections]
    assert "Description" in headers
    assert "Manifest: pyproject.toml" in headers
    assert sum(1 for _, _, is_code in sections if is_code) == 1


def test_code_section_uses_larger_window_than_prose() -> None:
    """A ~500-token body fits one code chunk (window 600) but splits into two
    prose chunks (window 400)."""
    tok = _WordTokenizer()
    body = " ".join(f"w{i}" for i in range(500))

    code_blob = CODE_FENCE_PREFIX + "big.py\n" + body
    code_chunks = chunk_text(code_blob, tokenizer=tok, window=400, code_window=600)
    assert len(code_chunks) == 1, [c.n_tokens for c in code_chunks]

    prose_chunks = chunk_text(body, tokenizer=tok, window=400, code_window=600)
    assert len(prose_chunks) == 2, [c.n_tokens for c in prose_chunks]


def test_fence_free_blob_chunks_as_before() -> None:
    """Regression guard: no fence → all sections prose (is_code=False) and the
    output matches the pre-Follow-up-4 markdown behavior."""
    tok = _WordTokenizer()
    blob = "# A\n" + " ".join(f"a{i}" for i in range(10)) + "\n\n# B\nshort body here"

    sections = split_into_sections(blob)
    assert all(is_code is False for _, _, is_code in sections)
    assert [h for h, _, _ in sections] == ["A", "B"]

    chunks = chunk_text(blob, tokenizer=tok, project_title="Proj")
    assert chunks  # non-empty
    # Section header + project tag still prefixed in-band.
    assert chunks[0].text.startswith("[Project: Proj]\n[Section: A]\n")


def test_code_window_overlap_validated() -> None:
    """code_overlap must be in [0, code_window) just like the prose window."""
    tok = _WordTokenizer()
    import pytest

    with pytest.raises(ValueError):
        chunk_text("x", tokenizer=tok, code_window=100, code_overlap=100)
