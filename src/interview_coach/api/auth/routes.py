import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from interview_coach.api.auth.deps import get_current_user
from interview_coach.api.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    ResetAccountRequest,
    TokenResponse,
    UserOut,
)
from interview_coach.api.auth.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from interview_coach.db import repos
from interview_coach.db.models import User
from interview_coach.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    existing = await repos.get_user_by_email(session, body.email)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = await repos.create_user(session, body.email, hash_password(body.password))
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    user = await repos.get_user_by_email(session, body.email)
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.model_validate(user)


async def _delete_checkpoint_threads_for_user(
    checkpointer: object,
    *,
    session_ids: list,
    job_ids: list,
    user_id,
) -> None:
    """Best-effort cleanup of every langgraph checkpoint thread owned by
    this user. Patterns:
      * ``prep:{user_id}:{job_id}``               — prep_graph per JD
      * ``{session_id}:turn_*``                   — interview graph per turn

    Failures are logged and swallowed so a flaky saver doesn't abort the
    account reset — the DB scrub is the source of truth and orphan
    threads can be GC'd separately.
    """
    if checkpointer is None:
        return
    adelete = getattr(checkpointer, "adelete_thread", None)
    if adelete is None:
        return

    # Per-job prep threads — pattern known, no enumeration needed.
    for jid in job_ids:
        try:
            await adelete(f"prep:{user_id}:{jid}")
        except Exception:  # noqa: BLE001
            logger.exception("adelete_thread failed for prep:%s:%s", user_id, jid)

    # Per-session turn threads — ``n`` is unbounded, so query the saver's
    # underlying connection for distinct thread_ids matching the prefix.
    conn = getattr(checkpointer, "conn", None)
    if conn is None or not session_ids:
        return
    for sid in session_ids:
        try:
            async with conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?",
                (f"{sid}:turn_%",),
            ) as cur:
                rows = await cur.fetchall()
            for (thread_id,) in rows:
                try:
                    await adelete(thread_id)
                except Exception:  # noqa: BLE001
                    logger.exception("adelete_thread failed for %s", thread_id)
        except Exception:  # noqa: BLE001
            # Saver table might be missing in a fresh install — never fatal.
            logger.exception("checkpoint thread enumeration failed for session %s", sid)


@router.post("/me/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_account(
    body: ResetAccountRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Phase 22 — wipe everything the user owns; keep the ``users`` row
    + auth so the user remains logged in with an empty account ready to
    re-onboard. Requires the caller to type their own email as a
    confirmation guard (case-insensitive match on ``current_user.email``).

    Order of operations: enumerate session + job ids FIRST so we have the
    keys for checkpoint thread cleanup, clear threads (best-effort), then
    DB scrub. Cascades on ``users.id`` mean we only need to delete the
    four top-level owned tables — turns/evaluations/grounding_chunks/
    document_mappings/company_snapshots all follow.
    """
    if body.confirm_email.lower() != user.email.lower():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "confirm_email does not match the authenticated user",
        )

    session_ids = await repos.list_all_session_ids_for_user(session, user.id)
    job_ids = await repos.list_job_ids_for_user(session, user.id)

    checkpointer = getattr(request.app.state, "checkpointer", None)
    await _delete_checkpoint_threads_for_user(
        checkpointer,
        session_ids=session_ids,
        job_ids=job_ids,
        user_id=user.id,
    )

    await repos.reset_user_data(session, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
