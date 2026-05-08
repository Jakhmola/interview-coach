"""Langfuse observability — opt-in via env.

When ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` are both set,
LangGraph runs are traced: one trace per ``astream`` call, with nested
spans for each node and ``ChatOpenAI`` generation. When either env var
is missing, every public function here is a no-op — zero SDK init,
zero network calls, zero noise. This matters for ``make test`` and for
contributors without Langfuse access.

Usage from the route layer::

    cb = langfuse_callback()  # may be None
    callbacks = [cb] if cb else []
    with trace_attributes(session_id=..., user_id=..., metadata=..., tags=...):
        async for chunk in graph.astream(..., config={"callbacks": callbacks}):
            ...

The ``trace_attributes`` context manager is also a no-op when Langfuse
is disabled, so route code reads the same in both modes.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager, nullcontext
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

_client_initialized: bool = False


def langfuse_enabled() -> bool:
    """True iff both required Langfuse env vars are set to a non-empty value."""
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


def _ensure_client_initialized() -> bool:
    """Lazily construct the global Langfuse client on first use.

    The Langfuse SDK reads ``LANGFUSE_*`` env vars itself, so we don't
    need to pass them through. Returns ``True`` when the client is
    available, ``False`` otherwise. Failures during init are logged
    and swallowed — observability must never break the app.
    """
    global _client_initialized
    if _client_initialized:
        return True
    if not langfuse_enabled():
        return False
    try:
        from langfuse import Langfuse

        Langfuse()
        _client_initialized = True
        logger.info(
            "Langfuse tracing enabled (host=%s)", os.environ.get("LANGFUSE_HOST", "default")
        )
        return True
    except Exception:
        logger.exception("Failed to initialize Langfuse client; tracing disabled")
        return False


def langfuse_callback() -> BaseCallbackHandler | None:
    """Return a Langfuse `CallbackHandler` ready to attach to a Runnable, or None.

    Per-trace attributes (user_id, session_id, metadata, tags) are NOT
    set on the handler itself — in Langfuse v4 they're applied via the
    ``trace_attributes`` context manager wrapping the graph call. The
    handler is otherwise stateless and could be cached, but
    constructing one is cheap so we don't bother.
    """
    if not _ensure_client_initialized():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:
        logger.exception("Failed to construct Langfuse CallbackHandler")
        return None


@contextmanager
def trace_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[None]:
    """Apply Langfuse trace attributes to spans created within this context.

    No-op when Langfuse is disabled. The context manager is the v4 SDK's
    standard way to attach a `session_id`, `user_id`, etc. to whatever
    LangChain runs inside.
    """
    if not _ensure_client_initialized():
        with nullcontext():
            yield
        return
    try:
        from langfuse import propagate_attributes

        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
            tags=tags,
        ):
            yield
    except Exception:
        logger.exception("Langfuse trace_attributes failed; running without attributes")
        yield


async def flush_langfuse(timeout: float = 2.0) -> None:
    """Flush queued spans on shutdown. No-op when Langfuse is disabled.

    The v4 SDK batches sends; if the api container stops without a
    flush we lose the last few spans. The flush is bounded by ``timeout``
    so a degraded Langfuse host can't block container shutdown.
    """
    if not _client_initialized:
        return
    try:
        import asyncio

        from langfuse import get_client

        client = get_client()
        # client.flush() is sync; run it off the event loop with a timeout.
        await asyncio.wait_for(asyncio.to_thread(client.flush), timeout=timeout)
    except TimeoutError:
        logger.warning("Langfuse flush timed out after %ss", timeout)
    except Exception:
        logger.exception("Langfuse flush failed")
