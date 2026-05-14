"""LLM adapter — `ChatOpenAI` factory pointed at a local OpenAI-compatible
server (we use `llama.cpp`'s `llama-server`).

Phase 5 originally targeted Ollama; we switched to llama.cpp for proper
tool-calling support and faster TTFT on GGUF quants. The factory is
backend-agnostic — anything that speaks OpenAI's `/v1/chat/completions`
will work (vLLM, llama.cpp, LM Studio, OpenAI proper, etc.).

Phase 6+ agents always pass `temperature` explicitly (see prompts in
`agents/prompts.py`).

Phase 16: every chat call records a telemetry row via `record_call`. The
two call shapes — ``ainvoke`` (structured output) and ``astream`` (token
streaming) — each have a wrapper. Tokens for streamed calls are pulled
from the final chunk's `usage_metadata` when llama.cpp returns it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_openai import ChatOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from interview_coach.config import settings
from interview_coach.llm.telemetry import extract_token_usage, record_call

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

    Telemetry: records one `llm_calls` row at end-of-stream (success or
    failure). Token counts come from the final chunk's `usage_metadata`
    when present.
    """
    llm = chat_model(temperature=temperature, **overrides)

    async for attempt in _retrying():
        with attempt:
            agen = llm.astream(messages)
            try:
                first = await agen.__anext__()
            except StopAsyncIteration:
                # Empty stream: record a successful zero-token call and return.
                await record_call(
                    model=settings.model_name,
                    latency_ms=0,
                    success=True,
                )
                return

    started = time.perf_counter()
    pt: int | None = None
    ct: int | None = None
    last_chunk: AIMessageChunk | None = None
    try:
        if first.content:
            yield _to_text(first.content)
        last_chunk = first
        async for chunk in agen:
            if chunk.content:
                yield _to_text(chunk.content)
            last_chunk = chunk
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await record_call(
            model=settings.model_name,
            latency_ms=latency_ms,
            success=False,
            error_class=e.__class__.__name__,
        )
        raise
    finally:
        if last_chunk is not None:
            usage = getattr(last_chunk, "usage_metadata", None) or getattr(
                last_chunk, "response_metadata", {}
            ).get("token_usage")
            pt, ct = extract_token_usage(usage)

    latency_ms = int((time.perf_counter() - started) * 1000)
    await record_call(
        model=settings.model_name,
        latency_ms=latency_ms,
        success=True,
        prompt_tokens=pt,
        completion_tokens=ct,
    )


async def ainvoke_with_telemetry(
    llm: BaseChatModel,
    messages: Sequence[BaseMessage],
    *,
    retry_count: int = 0,
) -> Any:
    """Call `llm.ainvoke(messages)` and record one telemetry row.

    `retry_count` is the number of retries the caller has already done before
    this final attempt — used by `chat_model_structured` (Commit 2).
    """
    started = time.perf_counter()
    try:
        result = await llm.ainvoke(messages)
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await record_call(
            model=settings.model_name,
            latency_ms=latency_ms,
            success=False,
            retry_count=retry_count,
            error_class=e.__class__.__name__,
        )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    pt, ct = _usage_from_result(result)
    await record_call(
        model=settings.model_name,
        latency_ms=latency_ms,
        success=True,
        retry_count=retry_count,
        prompt_tokens=pt,
        completion_tokens=ct,
    )
    return result


async def astream_with_telemetry(
    llm: BaseChatModel,
    messages: Sequence[BaseMessage],
) -> AsyncIterator[AIMessageChunk]:
    """Stream chunks from `llm.astream(messages)` and record one telemetry
    row at end-of-stream.

    Unlike `stream_text`, this yields raw `AIMessageChunk` objects so the
    streaming-JSON sites (question_generator, evaluator) can inspect `content`
    in their own way. Use `stream_text` for the simpler text-only callers.
    """
    started = time.perf_counter()
    last_chunk: AIMessageChunk | None = None
    try:
        async for chunk in llm.astream(messages):
            last_chunk = chunk
            yield chunk
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await record_call(
            model=settings.model_name,
            latency_ms=latency_ms,
            success=False,
            error_class=e.__class__.__name__,
        )
        raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    pt: int | None = None
    ct: int | None = None
    if last_chunk is not None:
        usage = getattr(last_chunk, "usage_metadata", None) or getattr(
            last_chunk, "response_metadata", {}
        ).get("token_usage")
        pt, ct = extract_token_usage(usage)
    await record_call(
        model=settings.model_name,
        latency_ms=latency_ms,
        success=True,
        prompt_tokens=pt,
        completion_tokens=ct,
    )


def _usage_from_result(result: Any) -> tuple[int | None, int | None]:
    """Pull token usage from an `ainvoke` result.

    For raw chat results: `result.usage_metadata` (LangChain) or
    `result.response_metadata['token_usage']` (OpenAI shape).

    For structured outputs (`with_structured_output`), the result is the
    parsed Pydantic model and usage isn't surfaced — return (None, None).
    """
    usage = getattr(result, "usage_metadata", None)
    if usage is None:
        meta = getattr(result, "response_metadata", None)
        if isinstance(meta, dict):
            usage = meta.get("token_usage")
    return extract_token_usage(usage)


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
