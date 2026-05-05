"""LLM adapter — `ChatOllama` factory and a streaming helper.

Phase 5: thin wrapper. Phase 6+ agents call `chat_model(temperature=...)`
and `stream_text(...)` directly. Temperature is **always passed explicitly
by agent code**; the default here is only for ad-hoc scripts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama
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
    """Build a `ChatOllama` configured from `Settings`.

    Agent code MUST pass `temperature` explicitly — different agents/prompts
    want different values (e.g., 0.0 for evaluator, 0.7 for question generator).

    `overrides` forwards any extra kwargs to `ChatOllama` (e.g., `top_p`,
    `num_predict`).
    """
    return ChatOllama(
        model=settings.model_name,
        base_url=settings.ollama_base_url,
        temperature=temperature,
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
            # Trigger the connection by pulling the first chunk.
            try:
                first = await agen.__anext__()
            except StopAsyncIteration:
                return

    # Yield the first chunk, then the rest. Inside the iteration, no retry.
    if first.content:
        yield _to_text(first.content)
    async for chunk in agen:
        if chunk.content:
            yield _to_text(chunk.content)


def _to_text(content: Any) -> str:
    """ChatOllama deltas are usually strings, but content can be a list of parts."""
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
