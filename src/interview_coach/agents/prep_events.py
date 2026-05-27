"""Prep-graph node-lifecycle events (Phase 27) — one typed owner for the wire.

Phase 26 made the skip *verdict* canonical (``prep_cache``); this module makes
the node-lifecycle *events* canonical. The prep graph's nodes emit four
events — ``node_started`` / ``node_skipped`` / ``node_done`` / ``error`` — and
historically each was a loose dict assembled inline, forwarded by a
hand-maintained route allowlist, and re-declared as an anonymous type on the
frontend: three copies of one contract, free to drift.

These pydantic models are that contract's single owner. They reuse
``prep_cache``'s ``RunReason`` / ``SkipReason`` Literals so the verdict reason
rides the wire verbatim:

* ``NodeStarted {node, reason}`` — *why* this node ran (the run reason Phase 26
  computed and then discarded).
* ``NodeSkipped {node, reason}`` — *why* this node skipped (skip reason).
* ``NodeDone {node, outcome, code?, detail?}`` — *how* the run finished. The
  ``degraded`` outcome replaces Phase 22's ad-hoc ``degraded: true`` boolean.
* ``NodeError {node?, code, detail?}``.

Reason is the **decision** axis (carried on start/skip); outcome is the
**result** axis (carried on done). See
``docs/adr/0001-prep-event-protocol.md`` for why "degraded" is deliberately
both a run reason and an outcome.

The doc-mapping HITL events (``mapping_*``, plus the per-doc ``node_started``
the mapping loop emits with ``document_id`` / ``remaining``) are a separate,
untyped sub-protocol — they carry no verdict reason and are out of this
module's scope.

``emit(writer, event)`` is the only way a node should push one of these; it
funnels through ``model_dump(exclude_none=True)`` so a bad reason Literal
raises at emit time instead of putting a junk dict on the wire.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel

from interview_coach.agents.prep_cache import RunReason, SkipReason, SkipVerdict


class NodeStarted(BaseModel):
    """A prep node decided to run; ``reason`` is the cache-miss run reason."""

    event: Literal["node_started"] = "node_started"
    node: str
    reason: RunReason


class NodeSkipped(BaseModel):
    """A prep node reused its cached output; ``reason`` is the skip reason."""

    event: Literal["node_skipped"] = "node_skipped"
    node: str
    reason: SkipReason


class NodeDone(BaseModel):
    """A prep node finished. ``outcome`` defaults to ``ok``; a ``degraded``
    run carries a ``code`` (and optional ``detail``) for the soft failure."""

    event: Literal["node_done"] = "node_done"
    node: str
    outcome: Literal["ok", "degraded"] = "ok"
    code: str | None = None
    detail: str | None = None


class NodeError(BaseModel):
    """A prep node hit a fatal failure. The route forwards this then
    terminates the stream."""

    event: Literal["error"] = "error"
    node: str | None = None
    code: str
    detail: str | None = None


PrepLifecycleEvent = NodeStarted | NodeSkipped | NodeDone | NodeError

# The route's forwarding allowlist sources the lifecycle half from here so it
# can never drift from the models above. Kept in lockstep by
# ``test_prep_events.py``.
LIFECYCLE_EVENT_NAMES: frozenset[str] = frozenset(
    {"node_started", "node_skipped", "node_done", "error"}
)


def emit(writer: Callable[[dict[str, Any]], Any], event: PrepLifecycleEvent) -> None:
    """Push a typed lifecycle event onto the graph's custom stream.

    ``exclude_none`` keeps the wire shape matching the optional fields in the
    contract (a clean ``node_done`` is ``{event, node, outcome}``; ``code`` /
    ``detail`` appear only on a degraded one)."""
    writer(event.model_dump(exclude_none=True))


def emit_verdict(
    writer: Callable[[dict[str, Any]], Any], *, node: str, verdict: SkipVerdict
) -> bool:
    """Emit ``NodeSkipped`` (skip) or ``NodeStarted`` (run) from a cache verdict.

    This is the one piece of verdict→lifecycle-event glue shared by the three
    linear prep nodes. Returns ``True`` when the verdict says skip, so the
    caller can early-return its cached payload; ``False`` when it ran the node.
    """
    if verdict.skip:
        emit(writer, NodeSkipped(node=node, reason=verdict.reason))
        return True
    emit(writer, NodeStarted(node=node, reason=verdict.reason))
    return False
