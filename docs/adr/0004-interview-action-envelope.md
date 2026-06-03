# The interview is a conversational thread loop streamed over one `/message` endpoint as a typed action envelope

**Status:** proposed (Phase 34) — flips to accepted on merge

## Context & decision

Through Phase 33 the interview ran on a fixed two-call rhythm: `POST /next_question`
streamed `token* → done`, `POST /answer` streamed
`score → feedback… → model_answer… → done`. The interview graph was linear
(`question_generator → await_answer → evaluator → END`), one run per turn, and the
session loop lived in route code (`next_question` counting turns and deciding
completion). This choreography is **static** — the frontend hardcodes what each
endpoint returns — and that is exactly what capped interview depth: the system could
only ask-grade-ask, never probe an answer, clarify a misread question, or nudge a
stuck candidate.

Phase 34 makes a topic a **thread** (see `CONTEXT.md`): a root question plus the
interviewer's follow-up **moves** (`probe` / `clarify` / `nudge`) on the same focus,
evaluated once at thread close. The interviewer's next move is chosen at runtime by a
conductor LLM call, so the response to a candidate message is no longer fixed.

We decided to:

- **Collapse the two endpoints into one chat exchange** — `POST /sessions/{id}/message`
  (empty body opens the session) whose SSE stream carries the interviewer's next move.
- **Move the session loop into the interview graph**, which spans candidate messages via
  `interrupt(...)` / `Command(resume=…)` on a checkpoint thread keyed to the **session** —
  exactly as `prep_graph` already spans `/prepare` + `/prepare/resume`.
- **Make the wire a typed action envelope** that *frames* the existing token streams:
  - `move {kind: question|probe|clarify|nudge, thread_index, message_id}` … `token*` …
    `move_done {anchors?}` — one interviewer utterance.
  - `evaluation {thread_index}` … `score` … `feedback_token*` … `feedback_done` …
    `model_answer_token*` … `model_answer_done` … `evaluation_done` — a thread-close
    evaluation (reuses the Phase-9 events verbatim).
  - `wrap {session_status: "complete"}` — the session terminal, reached when the
    interviewer `advance`s with the topic budget (`n_questions`) spent.

The graph interrupts after each move that awaits an answer; the stream ends there,
FE-awaiting, like prep.

## Considered options

- **Keep the two endpoints, just widen their response shapes.** Rejected: the
  next_question/answer split is built around the ask-then-grade rhythm a thread does not
  have — after an answer you might get a probe, an evaluation, or an evaluation followed
  by the next topic's question, so neither endpoint keeps a stable meaning. One chat
  endpoint is the honest shape.
- **Keep the loop in the route layer (one graph run per move).** Rejected: the
  within-thread branch — `probe/clarify/nudge → await again`; `advance → evaluate → next
  thread` — is a genuine loop with conditional routing, which is LangGraph's native shape
  and already how `prep_graph` works. Hand-rolling it in route code is the thing the
  Phase 21/30 refactors moved *away* from (and ADR 0002 keeps routing edge-defined).
- **Invent one unified stream event instead of reusing the Phase-9 token/score/feedback
  events.** Rejected: those events already work and the FE renders them; the envelope only
  adds *framing* (`move` / `evaluation` / `wrap`), keeping the diff small — the same
  restraint ADR 0001 used when it typed the new lifecycle layer and left the working
  sub-protocol alone.

## The surprising bit (why this ADR exists)

A reader will see the interview graph **interrupt mid-HTTP-response and end the stream
without reaching END**, and assume it is a bug or an abandoned request. It is neither.
Like the prep graph, an interview run is a *single logical graph execution that spans
many HTTP round-trips*: each `/message` call resumes the checkpointed session thread,
runs until the next `interrupt(...)` (awaiting the candidate), and returns; the graph is
**paused**, not finished. `session_status` stays `active` across many such calls; only
`wrap` marks the run's true END. The two-persistence-layer split makes this legible —
the SQLite checkpoint holds the *paused graph*, Postgres holds the *durable transcript*
— so a `/message` with an empty body on a fresh session and a `/message` resuming a
six-message thread hit the same endpoint and are told apart only by the checkpoint state.

This also **reverses the deliberate Phase-9 static-choreography decision.** That was the
right call for an ask-grade tool; it is the wrong call for a conversational interviewer.
Recording the reversal stops a future reader from "restoring" the simpler two-endpoint
contract without realizing it forecloses probing.

## Consequences

- The `move` / `evaluation` / `wrap` framing is a new wire contract that the graph, the
  route pump, and the hand-written `api.ts` discriminated union all depend on — moderately
  costly to reshape, like ADR 0001's lifecycle events.
- `next_question` + `answer` and their FE state machine are retired. `InterviewPage`
  becomes a transcript that appends moves and inlines an evaluation block at each thread
  close. The Phase-9 token/score/feedback/model_answer events survive *inside* the
  envelope.
- **Per-turn scoring is gone.** Scores, feedback, and model answers are thread-level (one
  per topic), so anything keyed on per-question turns — the Phase-12a eval harness,
  History rendering — moves to the thread grain.
- The interview checkpoint thread is keyed to the **session** (one paused run per session)
  rather than `{session_id}:turn_{n}` (one run per turn).
