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

ingest_sema: asyncio.Semaphore = asyncio.Semaphore(1)
