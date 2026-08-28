"""Process-wide concurrency budget for background ingest tasks.

CV embed, profile build, and project_doc embed all run as fire-and-forget
tasks on the api event loop. Without a shared sema they parallelise and
spike host CPU (see Phase 19 plan). One slot = sequential under the api
container's compose CPU quota.

Single-worker uvicorn assumption holds — a multi-worker deploy would need
a DB-level lock instead.
"""

from __future__ import annotations

import asyncio
import weakref


class _LoopLocalSemaphore:
    """One ``asyncio.Semaphore`` per running event loop, made on first use.

    A module-level ``asyncio.Semaphore`` binds to the first loop that touches
    it and then raises "bound to a different event loop" from every other -
    which, under pytest-asyncio's loop-per-test, is every test after the
    first. The api runs one loop, so it sees one semaphore either way.
    """

    def __init__(self, value: int) -> None:
        self._value = value
        self._by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )

    def _current(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sema = self._by_loop.get(loop)
        if sema is None:
            sema = self._by_loop[loop] = asyncio.Semaphore(self._value)
        return sema

    async def __aenter__(self) -> None:
        await self._current().acquire()

    async def __aexit__(self, *exc: object) -> None:
        self._current().release()


ingest_sema = _LoopLocalSemaphore(1)
