"""Per-call LLM telemetry.

`set_node_context("name")` is a contextmanager an agent node uses to label
the LLM calls that happen inside the `with` block. The label is async-safe
because `contextvars` propagate across `await`.

`record_call(...)` writes a row to `llm_calls` and emits a structured log
line. It is wrapped in a broad try/except so telemetry failures never
escape into the agent path — telemetry is observability, not a hard
dependency.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Iterator
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from interview_coach.db.models import LLMCall
from interview_coach.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_node_name_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_node_name", default=None
)


@contextlib.contextmanager
def set_node_context(name: str) -> Iterator[None]:
    """Tag every LLM call inside this block with `name`."""
    token = _node_name_var.set(name)
    try:
        yield
    finally:
        _node_name_var.reset(token)


def current_node_name() -> str | None:
    return _node_name_var.get()


def extract_token_usage(usage: Any) -> tuple[int | None, int | None]:
    """Best-effort pull of (prompt_tokens, completion_tokens) from a
    LangChain `usage_metadata` dict or OpenAI-style `token_usage` dict.

    Returns (None, None) when the shape is unrecognized or absent.
    """
    if not usage or not isinstance(usage, dict):
        return None, None
    # LangChain's normalized shape
    if "input_tokens" in usage or "output_tokens" in usage:
        pt = usage.get("input_tokens")
        ct = usage.get("output_tokens")
        return (int(pt) if pt is not None else None, int(ct) if ct is not None else None)
    # OpenAI-style passthrough
    if "prompt_tokens" in usage or "completion_tokens" in usage:
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        return (int(pt) if pt is not None else None, int(ct) if ct is not None else None)
    return None, None


async def record_call(
    *,
    model: str,
    latency_ms: int,
    success: bool,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    retry_count: int = 0,
    error_class: str | None = None,
    node_name: str | None = None,
) -> None:
    """Insert one `llm_calls` row + emit a log line. Never raises."""
    name = node_name if node_name is not None else current_node_name()
    logger.info(
        "llm_call node=%s model=%s latency_ms=%d retries=%d success=%s "
        "prompt_tokens=%s completion_tokens=%s error=%s",
        name,
        model,
        latency_ms,
        retry_count,
        success,
        prompt_tokens,
        completion_tokens,
        error_class,
    )
    try:
        async with AsyncSessionLocal() as s:
            s.add(
                LLMCall(
                    node_name=name,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                    success=success,
                    error_class=error_class,
                )
            )
            await s.commit()
    except (SQLAlchemyError, Exception) as e:  # noqa: BLE001
        # Telemetry must never break the agent. Log and swallow.
        logger.warning("llm_calls insert failed: %s", e)
