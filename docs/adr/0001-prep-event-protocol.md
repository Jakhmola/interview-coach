# Prep-graph node-lifecycle events are a typed protocol carrying the verdict reason

**Status:** accepted (Phase 27)

## Context & decision

Phase 26 made the prep cache **skip verdict** canonical (`agents/prep_cache.py`), but
deliberately left the SSE wire untouched: the verdict's typed **cache reason** rode along
only on `node_skipped`. When a node *ran*, the verdict's run reason
(`missing` / `stale` / `forced` / `degraded`) was computed and then discarded — so the
frontend could never tell *why* a node ran, and the OD-1 degraded self-heal (company research
re-attempting after a transient soft-fail) looked identical to an ordinary fresh run.

We decided to give the prep-graph **node-lifecycle** events (`node_started`, `node_skipped`,
`node_done`, `error`) a single typed owner (`agents/prep_events.py`, pydantic v2 models reusing
`prep_cache`'s `RunReason` / `SkipReason` Literals), and to carry the verdict reason on
**both** decisions:

- `node_started {node, reason}` — *why this node ran* (run reason).
- `node_skipped {node, reason}` — *why this node skipped* (skip reason; unchanged from Phase 26).
- `node_done {node, outcome: "ok" | "degraded", code?, detail?}` — *how the run finished*.

Reason is the **decision** axis (carried at decision time, on start/skip); outcome is the
**result** axis (carried at completion, on done).

## Considered options

- **Leave the wire as loose inline dicts, just add `reason` to `node_started`.** Rejected: the
  protocol would still have no owner — names live in a route allowlist, the shape in an anonymous
  frontend type — and the new field would drift like the rest.
- **Type the entire prep stream (lifecycle + the doc-mapping HITL events) in one module.**
  Rejected for Phase 27: the mapping events carry no verdict reason and are a separate, already
  well-shaped sub-protocol. Folding them in balloons the diff for no thesis payoff. They stay an
  untyped sub-protocol, to be typed later only if they earn it.
- **Re-validate each chunk at the route.** Rejected: the node's `emit()` is the single
  in-process validation gate; re-parsing at the route is a redundant round-trip and a new failure
  mode. The route stays a pure passthrough translator; only the lifecycle event *names* are
  sourced from `prep_events` so the allowlist can't drift from the schema.

## The surprising bit (why this ADR exists)

**"degraded" is deliberately both a run reason and a node outcome.** A future reader will see the
word twice and assume one is a mistake. It is not:

- `node_started {reason: "degraded"}` — the **prior** snapshot was a transiently-degraded
  placeholder, so this prep re-attempts research (a degraded snapshot is *stale*, per `CONTEXT.md`).
- `node_done {outcome: "degraded"}` — **this** run soft-failed and produced the placeholder.

Both faithfully reference the one domain term **degraded snapshot** (`CONTEXT.md`); they differ
only in *which* run's output they describe. The stream reads cleanly:
`node_started{reason:degraded}` → `node_done{outcome:degraded}` is literally "retried the degraded
one, still degraded." We kept the canonical word and disambiguated structurally (event + field)
rather than inventing a near-synonym.

## Consequences

The four lifecycle events are now a wire contract that node, route, and the hand-written `api.ts`
discriminated union all depend on — moderately costly to reshape later. Frontend *rendering* of
the run reason is intentionally **not** in this phase; it lands in the next deepening step
(roadmap node C), which is why Phase 27 ships with the reason riding the wire unused.
