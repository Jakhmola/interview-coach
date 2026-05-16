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

import logging
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError
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
    try:
        if first.content:
            yield _to_text(first.content)
        pt, ct = _merge_usage(pt, ct, first)
        async for chunk in agen:
            if chunk.content:
                yield _to_text(chunk.content)
            pt, ct = _merge_usage(pt, ct, chunk)
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
    pt: int | None = None
    ct: int | None = None
    try:
        async for chunk in llm.astream(messages):
            pt, ct = _merge_usage(pt, ct, chunk)
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
    await record_call(
        model=settings.model_name,
        latency_ms=latency_ms,
        success=True,
        prompt_tokens=pt,
        completion_tokens=ct,
    )


def _merge_usage(
    pt: int | None, ct: int | None, chunk: AIMessageChunk
) -> tuple[int | None, int | None]:
    """Pick up token counts from any chunk that carries them.

    llama.cpp (and OpenAI when `stream_options.include_usage=true`) sends
    `usage_metadata` on a *trailing* chunk after the final content delta —
    and then sometimes one more empty chunk after that. Tracking only the
    last chunk loses the usage row. Instead, merge whenever a chunk has it.
    """
    usage = getattr(chunk, "usage_metadata", None) or getattr(chunk, "response_metadata", {}).get(
        "token_usage"
    )
    new_pt, new_ct = extract_token_usage(usage)
    return (new_pt if new_pt is not None else pt, new_ct if new_ct is not None else ct)


def _usage_from_result(result: Any) -> tuple[int | None, int | None]:
    """Pull token usage from an `ainvoke` result.

    For raw chat results: `result.usage_metadata` (LangChain) or
    `result.response_metadata['token_usage']` (OpenAI shape).

    For `with_structured_output(..., include_raw=True)`, the result is a
    dict containing the raw `AIMessage` under "raw" — pull usage from there.
    """
    target: Any = result
    if isinstance(result, dict) and "raw" in result:
        target = result["raw"]
    usage = getattr(target, "usage_metadata", None)
    if usage is None:
        meta = getattr(target, "response_metadata", None)
        if isinstance(meta, dict):
            usage = meta.get("token_usage")
    return extract_token_usage(usage)


_logger = logging.getLogger(__name__)

_STRUCTURED_RETRY_EXC = (ValidationError, OutputParserException, ValueError)


async def chat_model_structured[T: BaseModel](
    schema: type[T],
    messages: Sequence[BaseMessage],
    *,
    temperature: float,
    **overrides: Any,
) -> T:
    """Call the LLM with `with_structured_output(schema)`; retry once on
    schema validation / JSON parse failure with a self-correction follow-up.

    Records one telemetry row per attempt. The second row carries
    `retry_count=1`.

    The retry message tells the model exactly what went wrong:
        Your previous output failed JSON schema validation.
        Error: {error}
        Return ONLY a valid JSON object matching the requested schema.

    Raises whatever exception the second attempt raised.
    """
    base = chat_model(temperature=temperature, **overrides)
    llm = base.with_structured_output(schema, method="json_schema", include_raw=True)
    msgs = list(messages)

    try:
        result = await ainvoke_with_telemetry(llm, msgs, retry_count=0)
        return _unwrap_structured(result, schema)
    except _STRUCTURED_RETRY_EXC as e:
        _logger.warning(
            "structured-output parse failed on first attempt (%s: %s); retrying once",
            e.__class__.__name__,
            e,
        )
        retry_msgs = list(msgs) + [
            HumanMessage(
                content=(
                    "Your previous output failed JSON schema validation.\n"
                    f"Error: {e}\n"
                    "Return ONLY a valid JSON object matching the requested schema. "
                    "Do not include any prose, markdown, or commentary."
                )
            )
        ]
        result = await ainvoke_with_telemetry(llm, retry_msgs, retry_count=1)
        return _unwrap_structured(result, schema)


def _unwrap_structured(result: Any, schema: type[BaseModel]) -> Any:
    """`with_structured_output(..., include_raw=True)` returns
    `{"raw": AIMessage, "parsed": <model>, "parsing_error": ... | None}`.
    Raise the parsing error if present; otherwise return the parsed model.
    """
    if isinstance(result, dict) and "parsed" in result:
        err = result.get("parsing_error")
        if err is not None:
            raise err if isinstance(err, BaseException) else ValueError(str(err))
        parsed = result["parsed"]
        if parsed is None:
            raise ValueError("structured-output returned no parsed value")
        return parsed
    return result


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
