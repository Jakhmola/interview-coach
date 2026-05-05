"""LLM adapter tests.

Most tests mock the underlying `ChatOllama` so `make test` stays fast and
doesn't depend on a live Ollama. The opt-in `test_real_ollama_streaming` test
hits the real model on host when `INTEGRATION=1` is set.
"""

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from interview_coach.llm import ollama as llm_module
from interview_coach.llm.ollama import chat_model, stream_text


def test_chat_model_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_module.settings, "model_name", "qwen3:8b")
    monkeypatch.setattr(llm_module.settings, "ollama_base_url", "http://example:11434")

    llm = chat_model(temperature=0.5)
    assert llm.model == "qwen3:8b"
    assert "example:11434" in llm.base_url
    assert llm.temperature == 0.5


def test_chat_model_default_temperature() -> None:
    llm = chat_model()
    assert llm.temperature == 0.2


def test_chat_model_forwards_overrides() -> None:
    llm = chat_model(temperature=0.0, num_predict=128)
    assert llm.temperature == 0.0
    assert llm.num_predict == 128


async def _async_iter(items: list) -> AsyncIterator:
    for it in items:
        yield it


async def test_stream_text_yields_tokens() -> None:
    chunks = [
        AIMessageChunk(content="Hello"),
        AIMessageChunk(content=" "),
        AIMessageChunk(content="world"),
    ]

    fake_llm = AsyncMock()
    fake_llm.astream = lambda _msgs: _async_iter(chunks)

    with patch.object(llm_module, "chat_model", return_value=fake_llm):
        tokens = [t async for t in stream_text([HumanMessage("hi")], temperature=0.2)]

    assert tokens == ["Hello", " ", "world"]


async def test_stream_text_skips_empty_deltas() -> None:
    chunks = [
        AIMessageChunk(content=""),
        AIMessageChunk(content="ok"),
        AIMessageChunk(content=""),
    ]

    fake_llm = AsyncMock()
    fake_llm.astream = lambda _msgs: _async_iter(chunks)

    with patch.object(llm_module, "chat_model", return_value=fake_llm):
        tokens = [t async for t in stream_text([HumanMessage("hi")], temperature=0.2)]

    assert tokens == ["ok"]


async def test_stream_text_retries_on_transient_connection_error() -> None:
    """The first connection attempt fails; the second succeeds."""
    call_count = {"n": 0}

    def astream(_msgs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("first attempt fails")
        return _async_iter([AIMessageChunk(content="recovered")])

    fake_llm = AsyncMock()
    fake_llm.astream = astream

    with patch.object(llm_module, "chat_model", return_value=fake_llm):
        tokens = [t async for t in stream_text([HumanMessage("hi")], temperature=0.2)]

    assert tokens == ["recovered"]
    assert call_count["n"] == 2


async def test_stream_text_does_not_retry_other_errors() -> None:
    """Non-retryable errors propagate on the first attempt."""

    def astream(_msgs):
        raise ValueError("not a network error")

    fake_llm = AsyncMock()
    fake_llm.astream = astream

    with patch.object(llm_module, "chat_model", return_value=fake_llm):
        with pytest.raises(ValueError, match="not a network error"):
            [t async for t in stream_text([HumanMessage("hi")], temperature=0.2)]


def test_to_text_handles_string_content() -> None:
    assert llm_module._to_text("hello") == "hello"


def test_to_text_handles_list_content() -> None:
    assert llm_module._to_text(["a", {"text": "b"}, "c"]) == "abc"


# --- Opt-in integration: hits real Ollama on host ---


@pytest.mark.skipif(
    os.environ.get("INTEGRATION") != "1",
    reason="Set INTEGRATION=1 to run; requires `ollama pull qwen3:8b` on host.",
)
async def test_real_ollama_streaming() -> None:
    """Stream from a real Ollama instance and assert we got non-empty text.

    Drains the generator to completion (closing it cleanly) instead of breaking
    early — that's the pattern callers should follow too.
    """
    tokens: list[str] = []
    agen = stream_text([HumanMessage("Say hello in three words.")], temperature=0.0)
    try:
        async for tok in agen:
            tokens.append(tok)
    finally:
        await agen.aclose()

    assert tokens, "expected at least one token from Ollama"
    assert any(c.isalpha() for c in "".join(tokens))
