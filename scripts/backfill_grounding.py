"""Re-embed every row in `documents` into `grounding_chunks`.

Idempotent — `embed_and_store_document` deletes prior chunks before insert,
so this is safe to re-run after schema changes or model swaps.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from interview_coach.db.models import Document
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.rag.ingest import embed_and_store_document

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


async def main() -> None:
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Document.id, Document.filename, Document.kind))
        rows = result.all()

    logger.info("backfilling %d documents", len(rows))
    for doc_id, filename, kind in rows:
        try:
            n = await embed_and_store_document(doc_id)
            logger.info("doc=%s kind=%s file=%s chunks=%d", doc_id, kind, filename, n)
        except Exception:  # noqa: BLE001
            logger.exception("doc=%s failed", doc_id)


if __name__ == "__main__":
    asyncio.run(main())
