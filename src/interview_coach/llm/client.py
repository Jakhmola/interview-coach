"""LLM adapter — `ChatOpenAI` factory pointed at a local OpenAI-compatible
server (we use `llama.cpp`'s `llama-server`).

Phase 5 originally targeted Ollama; we switched to llama.cpp for proper
tool-calling support and faster TTFT on GGUF quants. The factory is
backend-agnostic — anything that speaks OpenAI's `/v1/chat/completions`
will work (vLLM, llama.cpp, LM Studio, OpenAI proper, etc.).

Phase 6+ agents always pass `temperature` explicitly (see prompts in
`agents/prompts.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from interview_coach.config import settings

DEFAULT_TEMPERATURE = 0.2

_RETRYABLE_EXC = (httpx.ConnectError, httpx.ReadTimeout)


def chat_model(temperature: float = DEFAULT_TEMPERATURE, **overrides: Any) -> BaseChatModel:
    """Build a `ChatOpenAI` configured from `Settings`.

    Agent code MUST pass `temperature` explicitly — different agents/prompts
    want different values (e.g., 0.0 for evaluator, 0.7 for question generator).

    `overrides` forwards extra kwargs to `ChatOpenAI` (e.g., `max_tokens`,
    `top_p`, `model_kwargs`).

    The `api_key` value is irrelevant for local servers but the OpenAI client
    library requires it to be non-empty.
    """
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "not-needed",
        model=settings.model_name,
        temperature=temperature,
        stream_usage=True,
        **overrides,
    )


def _retrying() -> AsyncRetrying:
    return AsyncRetrying(
        retry=retry_if_exception_type(_RETRYABLE_EXC),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
        reraise=True,
    )


async def stream_text(
    messages: Sequence[BaseMessage],
    temperature: float = DEFAULT_TEMPERATURE,
    **overrides: Any,
) -> AsyncIterator[str]:
    """Stream the LLM reply as token-string deltas.

    Retries the *initial connection* on transient httpx errors (3 attempts,
    exponential backoff). Once the stream is open, errors propagate.
    """
    llm = chat_model(temperature=temperature, **overrides)

    async for attempt in _retrying():
        with attempt:
            agen = llm.astream(messages)
            try:
                first = await agen.__anext__()
            except StopAsyncIteration:
                return

    if first.content:
        yield _to_text(first.content)
    async for chunk in agen:
        if chunk.content:
            yield _to_text(chunk.content)


def _to_text(content: Any) -> str:
    """ChatOpenAI deltas are usually strings, but content can be a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and "text" in p:
                parts.append(str(p["text"]))
        return "".join(parts)
    return str(content)
