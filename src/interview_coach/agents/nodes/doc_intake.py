"""Project_doc intake — runs once per project_doc upload.

Workflow:
  1. ``run_intake(document_id)`` — single LLM call that returns
     ``{title, extracted, suggestions}``. Persists ``project_title`` on the
     document row. Returns the payload for the HITL frontend modal.
  2. ``apply_mapping(document_id, rows)`` — accepts user-confirmed mapping
     rows, mutates the profile (enrich existing Highlight / append new
     Highlight / append new ProjectItem), persists the ``document_mappings``
     rows with the extracted payload, and kicks off chunking.
  3. ``revert_mapping(document_id)`` — undoes (1) and (2) on doc deletion.
     Called from the document delete route BEFORE the document row is gone
     (we need ``document_mappings.extracted_json`` and the doc's ``id``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from interview_coach.agents.prompts import DOC_INTAKE_SYSTEM
from interview_coach.agents.schemas import DocIntakeResult, Profile
from interview_coach.config import settings
from interview_coach.db import repos
from interview_coach.db.session import AsyncSessionLocal
from interview_coach.llm.client import chat_model_structured
from interview_coach.llm.telemetry import set_node_context
from interview_coach.rag.concurrency import ingest_sema
from interview_coach.rag.ingest import embed_and_store_document

logger = logging.getLogger(__name__)

MAX_DOC_TEXT_CHARS = 3000


class DocIntakeError(Exception):
    """Wraps any failure during the intake LLM call."""


class ProfileMissing(Exception):
    """Apply-mapping called before the CV-based profile exists."""


def _format_experiences_for_prompt(profile_json: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, exp in enumerate(profile_json.get("experiences") or []):
        highlights = exp.get("highlights") or []
        out.append(
            {
                "experience_idx": i,
                "company": exp.get("company", ""),
                "role": exp.get("role", ""),
                "highlights": [
                    {"highlight_idx": j, "text": h.get("text", "")}
                    for j, h in enumerate(highlights)
                    if isinstance(h, dict)
                ],
            }
        )
    return out


async def run_intake(document_id: uuid.UUID, user_id: uuid.UUID) -> DocIntakeResult:
    """Title + extract + suggest for a project_doc. Persists ``project_title``.

    Raises:
        DocIntakeError: LLM didn't return a valid DocIntakeResult.
        ProfileMissing: profile not yet built (CV not uploaded).
    """
    async with AsyncSessionLocal() as s:
        doc = await repos.get_document(s, document_id, user_id)
        if doc is None:
            raise DocIntakeError(f"document {document_id} not found")
        if doc.kind != "project_doc":
            raise DocIntakeError(f"intake only supports project_doc, got {doc.kind!r}")
        profile_row = await repos.get_profile(s, user_id)
    if profile_row is None:
        raise ProfileMissing("upload your CV and build a profile before adding project docs")

    doc_text = doc.raw_text[:MAX_DOC_TEXT_CHARS]
    payload = {
        "doc_text": doc_text,
        "experiences": _format_experiences_for_prompt(profile_row.profile_json),
    }

    with set_node_context("doc_intake"):
        try:
            import json as _json

            result = await chat_model_structured(
                DocIntakeResult,
                [
                    SystemMessage(content=DOC_INTAKE_SYSTEM),
                    HumanMessage(content=_json.dumps(payload, ensure_ascii=False, indent=2)),
                ],
                temperature=0.0,
            )
        except ValidationError as e:
            raise DocIntakeError(f"doc-intake JSON failed schema validation: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise DocIntakeError(f"doc-intake call failed: {e}") from e
    assert isinstance(result, DocIntakeResult)

    title = result.title.strip()[:160] or doc.filename
    async with AsyncSessionLocal() as s:
        await repos.update_document_title(s, document_id, user_id, title)

    logger.info(
        "doc_intake: doc=%s title=%r suggestions=%d",
        document_id,
        title,
        len(result.suggestions),
    )
    return result.model_copy(update={"title": title})


MAPPING_PREVIEW_CHARS = 1500


def build_mapping_suggestion_payload(
    *,
    document_id: uuid.UUID,
    intake: DocIntakeResult,
    doc_raw_text: str,
    profile_json: dict[str, Any] | None,
    remaining: int,
) -> dict[str, Any]:
    """Build the ``mapping_suggestion`` payload consumed by the FE
    ``MappingModal``. Shared between the prep_graph node and the
    out-of-graph remap route (Phase 22) so both surfaces render the
    exact same shape — no FE branching on "where did this come from"."""
    experiences: list[dict[str, Any]] = []
    if profile_json is not None:
        for i, exp in enumerate(profile_json.get("experiences") or []):
            if not isinstance(exp, dict):
                continue
            highlights_out: list[dict[str, Any]] = []
            for j, hl in enumerate(exp.get("highlights") or []):
                if isinstance(hl, dict):
                    highlights_out.append({"highlight_idx": j, "text": str(hl.get("text") or "")})
            experiences.append(
                {
                    "experience_idx": i,
                    "company": str(exp.get("company") or ""),
                    "role": str(exp.get("role") or ""),
                    "highlights": highlights_out,
                }
            )
    return {
        "document_id": str(document_id),
        "title": intake.title,
        "preview": doc_raw_text[:MAPPING_PREVIEW_CHARS],
        "extracted": intake.extracted.model_dump(),
        "suggestions": [s.model_dump() for s in intake.suggestions],
        "experiences": experiences,
        "remaining": remaining,
    }


def _resolve_extracted_payload(
    *,
    user_supplied: dict[str, Any] | None,
    fallback: dict[str, Any] | None,
) -> dict[str, Any]:
    """Mapping rows may carry their own extracted payload (rare); otherwise
    use the single doc-level extracted payload from the intake call.
    """
    if user_supplied:
        return user_supplied
    return fallback or {}


def reapply_existing_mappings(profile: Profile, mapping_rows: list[dict[str, Any]]) -> Profile:
    """Phase 21.1: re-apply every persisted ``document_mappings`` row to a
    freshly-built profile so a CV re-extract doesn't silently wipe prior
    project_doc enrichments.

    ``mapping_rows`` shape (one row per persisted ``document_mappings``):
        ``{document_id: uuid, mapping_kind, experience_idx?, highlight_idx?,
           project_idx?, extracted_json?}``

    Each row is grouped by ``document_id`` and fed through the same
    ``_mutate_profile_apply`` pipeline ``apply_mapping`` uses — same
    semantics, same result. Rows whose indices no longer resolve against
    the rebuilt profile (e.g. the user removed an experience from their
    CV) are skipped with a log line, not raised — losing a mapping is
    recoverable, blowing up the whole profile build is not.
    """
    if not mapping_rows:
        return profile

    by_doc: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for r in mapping_rows:
        by_doc.setdefault(r["document_id"], []).append(r)

    current = profile
    for doc_id, rows in by_doc.items():
        doc_extracted = (
            next(
                (r["extracted_json"] for r in rows if r.get("extracted_json")),
                {},
            )
            or {}
        )
        try:
            current = _mutate_profile_apply(
                profile=current,
                doc_id=doc_id,
                rows=rows,
                doc_extracted=doc_extracted,
            )
        except ValueError as e:
            logger.warning(
                "reapply_existing_mappings: dropping mappings for doc=%s — indices no "
                "longer resolve after CV rebuild (%s)",
                doc_id,
                e,
            )
    return current


def _mutate_profile_apply(
    *,
    profile: Profile,
    doc_id: uuid.UUID,
    rows: list[dict[str, Any]],
    doc_extracted: dict[str, Any],
) -> Profile:
    """Pure: return a new Profile with mappings applied.

    `rows` items: ``{mapping_kind, experience_idx?, highlight_idx?, project_idx?,
    extracted_json?}``. Each row records (and we apply) what *this doc* adds.
    """
    profile_dict = profile.model_dump(mode="json")
    experiences = profile_dict.setdefault("experiences", [])
    projects = profile_dict.setdefault("projects", [])

    for row in rows:
        kind = row["mapping_kind"]
        extracted = _resolve_extracted_payload(
            user_supplied=row.get("extracted_json"), fallback=doc_extracted
        )
        tech = list(extracted.get("tech_stack") or [])
        description = extracted.get("description")
        urls = list(extracted.get("urls") or [])

        if kind == "highlight":
            exp_idx = row["experience_idx"]
            hl_idx = row["highlight_idx"]
            try:
                exp = experiences[exp_idx]
                hl = exp["highlights"][hl_idx]
            except (IndexError, KeyError) as e:
                raise ValueError(
                    f"highlight mapping points at experience_idx={exp_idx} "
                    f"highlight_idx={hl_idx} which does not exist"
                ) from e
            doc_id_str = str(doc_id)
            src_ids = hl.setdefault("source_document_ids", [])
            if doc_id_str not in src_ids:
                src_ids.append(doc_id_str)
            existing_tech = hl.setdefault("tech_stack", [])
            for t in tech:
                if t and t not in existing_tech:
                    existing_tech.append(t)
            existing_urls = hl.setdefault("urls", [])
            for u in urls:
                if u and u not in existing_urls:
                    existing_urls.append(u)
            if description and not hl.get("description"):
                hl["description"] = description

        elif kind == "experience":
            exp_idx = row["experience_idx"]
            try:
                exp = experiences[exp_idx]
            except IndexError as e:
                raise ValueError(
                    f"experience mapping points at experience_idx={exp_idx} which does not exist"
                ) from e
            hl_list = exp.setdefault("highlights", [])
            hl_list.append(
                {
                    "text": description or extracted.get("description") or "Project work",
                    "tech_stack": tech,
                    "description": description,
                    "urls": urls,
                    "source_document_ids": [str(doc_id)],
                }
            )

        elif kind == "project":
            projects.append(
                {
                    "name": "",  # filled by API caller with the doc title
                    "description": description or "",
                    "tech": tech,
                    "role": None,
                    "urls": urls,
                    "source": "project_doc",
                    "source_document_ids": [str(doc_id)],
                }
            )
        else:
            raise ValueError(f"unknown mapping_kind {kind!r}")

    return Profile.model_validate(profile_dict)


def _mutate_profile_revert(
    *,
    profile: Profile,
    doc_id: uuid.UUID,
    mapping_rows: list[dict[str, Any]],
) -> Profile:
    """Pure: return a new Profile with the contributions of `doc_id` removed.

    For ``highlight``: drop ``doc_id`` from ``source_document_ids``, subtract
    the tech/url items captured in ``extracted_json``, clear ``description``
    if and only if this doc supplied it (best effort: cleared if it still
    matches).

    For ``experience``: drop the Highlight rows whose ONLY contributor was
    this doc.

    For ``project``: drop the ProjectItem rows whose ONLY contributor was
    this doc.
    """
    profile_dict = profile.model_dump(mode="json")
    doc_id_str = str(doc_id)

    # --- pass 1: highlight enrichments --
    for row in mapping_rows:
        if row["mapping_kind"] != "highlight":
            continue
        exp_idx = row["experience_idx"]
        hl_idx = row["highlight_idx"]
        try:
            hl = profile_dict["experiences"][exp_idx]["highlights"][hl_idx]
        except (IndexError, KeyError, TypeError):
            continue
        extracted = row.get("extracted_json") or {}
        tech = set(extracted.get("tech_stack") or [])
        urls = set(extracted.get("urls") or [])
        description = extracted.get("description")
        if doc_id_str in hl.get("source_document_ids", []):
            hl["source_document_ids"] = [s for s in hl["source_document_ids"] if s != doc_id_str]
        hl["tech_stack"] = [t for t in hl.get("tech_stack") or [] if t not in tech]
        hl["urls"] = [u for u in hl.get("urls") or [] if u not in urls]
        if description and hl.get("description") == description:
            hl["description"] = None

    # --- pass 2: experience-level appended highlights and projects ---
    # Collect target indices, descending so we can pop safely.
    exp_drops: list[tuple[int, int]] = []
    proj_drops: list[int] = []
    for row in mapping_rows:
        kind = row["mapping_kind"]
        if kind == "experience":
            # Find which highlight on this experience was sourced ONLY by doc_id.
            exp_idx = row["experience_idx"]
            try:
                hl_list = profile_dict["experiences"][exp_idx]["highlights"]
            except (IndexError, KeyError, TypeError):
                continue
            for hl_idx, hl in enumerate(hl_list):
                src = hl.get("source_document_ids") or []
                if src == [doc_id_str]:
                    exp_drops.append((exp_idx, hl_idx))
        elif kind == "project":
            for p_idx, proj in enumerate(profile_dict.get("projects") or []):
                src = proj.get("source_document_ids") or []
                if src == [doc_id_str]:
                    proj_drops.append(p_idx)

    # Drop in reverse to keep indices valid.
    for exp_idx, hl_idx in sorted(set(exp_drops), reverse=True):
        try:
            del profile_dict["experiences"][exp_idx]["highlights"][hl_idx]
        except (IndexError, KeyError, TypeError):
            pass
    for p_idx in sorted(set(proj_drops), reverse=True):
        try:
            del profile_dict["projects"][p_idx]
        except (IndexError, TypeError):
            pass

    return Profile.model_validate(profile_dict)


async def apply_mapping(
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    rows: list[dict[str, Any]],
    extracted: dict[str, Any],
    project_title: str,
) -> int:
    """Persist `rows` as `document_mappings`, mutate the profile, kick off chunking.

    `rows` items must NOT include ``extracted_json`` from the caller — we stamp
    each row with the doc-level `extracted` payload so deletion can revert.
    For ``project`` rows we fill the ProjectItem's `name` with `project_title`.

    Returns the number of mapping rows written.
    """
    async with AsyncSessionLocal() as s:
        profile_row = await repos.get_profile(s, user_id)
    if profile_row is None:
        raise ProfileMissing("apply_mapping needs a profile; upload your CV first")

    profile = Profile.model_validate(profile_row.profile_json)

    # Stamp extracted_json onto each mapping row so deletion can subtract.
    stamped: list[dict[str, Any]] = []
    for row in rows:
        copy = {
            "mapping_kind": row["mapping_kind"],
            "experience_idx": row.get("experience_idx"),
            "highlight_idx": row.get("highlight_idx"),
            "project_idx": row.get("project_idx"),
            "extracted_json": extracted,
        }
        stamped.append(copy)

    new_profile = _mutate_profile_apply(
        profile=profile,
        doc_id=document_id,
        rows=stamped,
        doc_extracted=extracted,
    )
    # Fill project name from title where applicable.
    new_dict = new_profile.model_dump(mode="json")
    if new_dict.get("projects"):
        for proj in new_dict["projects"]:
            if proj.get("source") == "project_doc" and not proj.get("name"):
                proj["name"] = project_title
    new_profile = Profile.model_validate(new_dict)

    # Phase 21 (G4 fix): the profile_builder cache key in
    # `graph_nodes.node_profile_builder` compares the user's current
    # document set against `source_doc_ids`. Without folding the new
    # project_doc id in here, every subsequent prep run sees a mismatch
    # and re-runs profile_builder unnecessarily.
    new_source_ids = sorted(
        {*(str(x) for x in (profile_row.source_doc_ids or [])), str(document_id)}
    )

    async with AsyncSessionLocal() as s:
        await repos.replace_document_mappings(
            s,
            document_id=document_id,
            user_id=user_id,
            rows=stamped,
        )
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json=new_profile.model_dump(mode="json"),
            source_doc_ids=new_source_ids,
            model_name=settings.model_name,
        )

    # Chunking is deferred until mapping is confirmed (so chunks carry the
    # final user-edited project_title). Fire-and-forget under the shared
    # ingest sema so the apply-mapping POST returns immediately; the UI
    # polls `embedding_status` to discover when the chunks land.
    async def _embed_swallow(doc_id: uuid.UUID) -> None:
        async with ingest_sema:
            try:
                await embed_and_store_document(doc_id)
            except Exception:  # noqa: BLE001
                logger.exception("embed_and_store_document failed for doc=%s", doc_id)

    asyncio.create_task(_embed_swallow(document_id))

    return len(stamped)


async def revert_mapping(*, document_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Undo enrichments a project_doc applied to the profile.

    Called from the delete route. Idempotent: safe to invoke even when no
    mapping rows exist (e.g. CV docs, or a project_doc deleted before the
    user confirmed the mapping modal).
    """
    async with AsyncSessionLocal() as s:
        mapping_rows = await repos.list_document_mappings(s, document_id)
        profile_row = await repos.get_profile(s, user_id)

    if not mapping_rows or profile_row is None:
        return

    rows_as_dict = [
        {
            "mapping_kind": r.mapping_kind,
            "experience_idx": r.experience_idx,
            "highlight_idx": r.highlight_idx,
            "project_idx": r.project_idx,
            "extracted_json": r.extracted_json or {},
        }
        for r in mapping_rows
    ]
    profile = Profile.model_validate(profile_row.profile_json)
    new_profile = _mutate_profile_revert(
        profile=profile, doc_id=document_id, mapping_rows=rows_as_dict
    )

    # Phase 21 (G4 fix): drop the project_doc id from source_doc_ids so the
    # profile_builder cache key stays in sync with the user's documents
    # after this doc is deleted.
    new_source_ids = sorted(
        str(x) for x in (profile_row.source_doc_ids or []) if str(x) != str(document_id)
    )

    async with AsyncSessionLocal() as s:
        await repos.upsert_profile(
            s,
            user_id=user_id,
            profile_json=new_profile.model_dump(mode="json"),
            source_doc_ids=new_source_ids,
            model_name=settings.model_name,
        )
    # mapping rows + chunks cascade-delete with the document row.
