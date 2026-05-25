"""Prep cache verdicts (Phase 26) — one owner for "skip or run?".

Each prep-graph node decides whether to reuse a cached output or recompute.
Historically that decision was inlined into every node, and the
profile-cache key in particular was computed by four divergent formulas
that agreed only by accident. This module collapses the *decisions* into
a handful of pure functions returning a typed :class:`SkipVerdict`.

The verdict carries a typed **cache reason** (see ``CONTEXT.md``). The
reason strings are deliberately equal to the literals the frontend
already reads off ``node_skipped`` events — this phase changes no wire
format. Emitting the event is the node's job; the verdict only decides.

Pure and in-process: no DB, no LLM. Callers pass already-loaded state in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# skip=True reasons — reuse the cached output.
SkipReason = Literal["cached", "already_analyzed", "no_unmapped_project_docs"]
# skip=False reasons — recompute.
RunReason = Literal["missing", "stale", "forced", "degraded"]

# Degraded snapshot reasons that are worth retrying (OD-1). Both are
# transient: search or extraction flaked, so a re-run can succeed.
# ``CompanyNameMissing`` is structural (the analyzer found no company
# name) — re-running can't help — so it stays a cache hit until the user
# re-analyzes the JD via the ``force_refresh`` path.
_RETRY_WORTHY_DEGRADED = frozenset({"NoSearchHits", "NoUsablePages"})


@dataclass(frozen=True)
class SkipVerdict:
    """A node's decision to skip (reuse cache) or run (recompute)."""

    skip: bool
    reason: SkipReason | RunReason

    @classmethod
    def hit(cls, reason: SkipReason) -> SkipVerdict:
        return cls(skip=True, reason=reason)

    @classmethod
    def miss(cls, reason: RunReason) -> SkipVerdict:
        return cls(skip=False, reason=reason)


def _normalize(ids: Sequence[str] | None) -> list[str]:
    """Order- and duplicate-insensitive view of a doc-id set."""
    return sorted({str(x) for x in (ids or [])})


def decide_profile_cache(
    *,
    profile_exists: bool,
    stored_doc_ids: Sequence[str] | None,
    current_doc_ids: Sequence[str],
) -> SkipVerdict:
    """Skip ``profile_builder`` iff a profile exists and its stored
    Profile-document-set equals the current one (order/dup-insensitive)."""
    if not profile_exists:
        return SkipVerdict.miss("missing")
    if _normalize(stored_doc_ids) == _normalize(current_doc_ids):
        return SkipVerdict.hit("cached")
    return SkipVerdict.miss("stale")


def decide_job_cache(*, parsed_json: dict | None) -> SkipVerdict:
    """Skip ``job_analyzer`` iff the job already has parsed analysis."""
    if parsed_json:
        return SkipVerdict.hit("already_analyzed")
    return SkipVerdict.miss("missing")


def is_degraded_snapshot(snapshot_json: dict | None) -> bool:
    return "_degraded" in (snapshot_json or {})


def decide_company_cache(
    *,
    snapshot_json: dict | None,
    force_refresh: bool,
) -> SkipVerdict:
    """Decide ``company_researcher``. A degraded snapshot is stale (it
    re-attempts research) only for transient failures — see OD-1 and
    :data:`_RETRY_WORTHY_DEGRADED`."""
    if force_refresh:
        return SkipVerdict.miss("forced")
    if snapshot_json is None:
        return SkipVerdict.miss("missing")
    if is_degraded_snapshot(snapshot_json):
        if str(snapshot_json.get("_degraded")) in _RETRY_WORTHY_DEGRADED:
            return SkipVerdict.miss("degraded")
        return SkipVerdict.hit("cached")
    return SkipVerdict.hit("cached")
