# Prep/interview routing stays edge-defined; we do not adopt `Command(goto=…)`

**Status:** accepted (Phase 30)

## Context & decision

Both graphs (`prep_graph`, `interview_graph`) are built in `agents/graph.py` with
`StateGraph` edges: mostly `add_edge` (static) plus one `add_conditional_edges` out of
`prepare_mapping_suggestion` that drives the doc-mapping HITL loop. LangGraph also offers an
alternative where a node returns `Command(goto=…)` to pick its own successor, folding routing
into the node body.

Recurring architecture reviews keep surfacing "migrate the graphs to `Command(goto)`" as a
deepening idea. We are recording the decision **not** to: routing stays in the graph topology,
expressed as edges. Nodes return only a state delta; they never name their successor.

A direct consequence, settled in this same phase (Part A): the `next_step` state field is
**vestigial on every node except `prepare_mapping_suggestion`**. Only that node's conditional
edge reads `state["next_step"]`; every other edge is static, so the field was being set and
never read. Phase 30 removes those dead writes and scopes `next_step` to the one node whose
edge consumes it. Adopting `Command(goto)` would have pulled in the opposite direction —
spreading routing decisions back into node bodies.

## Considered options

- **Adopt `Command(goto=…)` across both graphs.** Rejected. The graphs are small, working, and
  checkpointed; the topology is already legible at a glance in `graph.py`. Moving routing into
  node returns buys no capability we need and disperses the control flow across nine node
  functions instead of one edge table.
- **Adopt `Command(goto)` only for the doc-mapping loop** (the one conditional edge). Rejected.
  It is the single place routing is dynamic — the best-understood, best-tested edge in the
  system (`test_phase21_prep_graph`, the mid-prep resume path). Rewriting precisely the part
  that resume-replays is the highest-risk, lowest-reward swap available.
- **Keep edges (chosen).** Routing lives in `graph.py`; `next_step` is the one explicit routing
  variable and is scoped to its single reader.

## The surprising bit (why this ADR exists)

The graphs are **checkpointed**, and `Command(goto)` changes how a resumed run re-derives its
next step. A reviewer who only reads node bodies will see plain state-delta returns and assume
`Command(goto)` is a free, obvious modernization. It is not free here: any routing change is a
resume-correctness change, validated only by the mid-prep kill/restart smoke test, not by host
unit tests. The cost is paid in a place that doesn't show up in a diff review.

## Consequences

Future reviews can treat "switch to `Command(goto)`" as already-decided and skip re-litigating
it. New routing remains a `graph.py` edge, not a node-body branch. If a genuinely dynamic,
data-dependent fan-out ever appears that edges express awkwardly, this ADR should be revisited
for that case specifically — not adopted wholesale.
