"""Structured error bodies shared across API routes.

Phase 22: the previous bare-string ``cv_in_use`` / ``job_in_use`` 409s
told the FE *what* failed but not *which* sessions were blocking, so the
only recourse was "delete sessions manually somewhere else and try
again." The new shape ships the offending session ids inline so Manage
can render per-session Abandon buttons.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel


class BlockingSessionsConflict(BaseModel):
    """Body shape for 409 responses from delete routes that are blocked
    by active sessions."""

    code: Literal["cv_in_use", "job_in_use"]
    blocking_session_ids: list[uuid.UUID]


def blocking_sessions_http_exception(
    *,
    code: Literal["cv_in_use", "job_in_use"],
    blocking_session_ids: list[uuid.UUID],
) -> HTTPException:
    """Build the 409 ``HTTPException`` with a JSON-serializable detail
    matching ``BlockingSessionsConflict``."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": code,
            "blocking_session_ids": [str(sid) for sid in blocking_session_ids],
        },
    )
