# Interview Coach — Master Plan

> **Note (Phase 18):** the user-facing UI is now a React + TypeScript app under `frontend/` (built by `Dockerfile.ui`). The old Streamlit `ui/` directory was removed in Phase 18. Mentions of Streamlit, `ui/pages/*.py`, and `st.*` in earlier phase descriptions below are historical — see `frontend/src/` for the live UI.

## Context

Greenfield project (only `requirements.txt` + `uv.lock` exist) to build a webapp that helps a candidate prepare for a specific job. Flow: candidate uploads CV/project docs, supplies a job description (raw or URL), the system researches the company, then runs a per-round interview where it asks personalized questions, scores answers, gives feedback, and shows a model answer. v1 targets two round types — **Resume / Project Deep-Dive** and **Behavioral / STAR**.

The stack: FastAPI + React/TypeScript (Vite, replaced the original Streamlit
UI in Phase 18) + Postgres (pgvector) + a separate FastAPI embedder sidecar
(Jina embeddings v3) + LangGraph (multi-agent supervisor) + LangChain + MCP
(Tavily for web, custom server for app-side tools) + Ollama on host (qwen3:8b)
+ Docker Compose. Multi-user, JWT + bcrypt auth. SSE streaming with
AbortController on the client. A2A wrapping deferred. STT, Markdown ingestion,
technical/system-design rounds — all out of v1.

The plan is organized as bricks: each phase is independently testable end-to-end before the next is started.

---

## High-level architecture

```
┌─ React + TS UI (container, Vite) ───────┐        Ollama on host
│  routes: /login / /setup / /interview / │        host.docker.internal:11434
│  /history; typed api.ts client → SSE    │                ▲
└──────────────┬──────────────────────────┘                │
               │  HTTPS + JWT                              │
┌──────────────▼──────────────────────────┐                │
│  FastAPI (container)                    │                │
│   /auth, /documents, /jobs, /sessions   │                │
│   SSE streaming for question/feedback   │                │
│                                         │                │
│   LangGraph supervisor StateGraph ──────┼────────────────┘
│     nodes: profile_builder, job_analyzer│        ChatOllama
│            company_researcher,          │
│            question_generator, evaluator│
│   tools via langchain-mcp-adapters ─────┼──┐
│   graph checkpointer: SQLite (sidecar)  │  │
└──────────────┬──────────────────────────┘  │
               │ SQLAlchemy async            │ stdio/HTTP
       ┌───────▼────────┐         ┌──────────▼──────────┐
       │  Postgres       │         │ MCP servers         │
       │  (container)    │         │  - tavily-mcp (img) │
       │  app data       │         │  - documents_server │
       └────────────────┘         │    (custom, in-proc │
                                  │     or stdio)       │
                                  └─────────────────────┘
```

Two persistence layers, intentionally separated:
- **Postgres** — app data: users, documents (raw + parsed), jobs, company snapshots, sessions, turns, evaluations.
- **SQLite (file volume)** — LangGraph checkpoints (matches `langgraph-checkpoint-sqlite==3.0.3` already in deps). Keeps graph state recovery decoupled from app schema.

---

## Repo structure

```
interview_coach/
  pyproject.toml              # convert from requirements.txt → uv-managed
  docker-compose.yml
  Dockerfile.api
  Dockerfile.ui
  .env.example
  alembic.ini
  alembic/versions/
  src/interview_coach/
    config.py                 # pydantic-settings, all env vars
    api/
      main.py                 # FastAPI app + lifespan (graph, mcp client)
      auth/{routes,deps,security}.py
      documents/routes.py
      jobs/routes.py
      sessions/routes.py
      streaming.py            # SSE helper
    agents/
      graph.py                # StateGraph + supervisor
      state.py                # InterviewState TypedDict
      nodes/
        profile_builder.py
        job_analyzer.py
        company_researcher.py
        question_generator.py
        evaluator.py
      prompts/                # Jinja-style templates per node
    mcp/
      client.py               # MultiServerMCPClient bootstrap
      servers/documents_server.py   # custom MCP server
    db/
      models.py               # SQLAlchemy 2.0 declarative
      session.py              # async engine + session
      repos.py                # query helpers used by routes
    ingestion/
      pdf.py                  # pypdf
      docx.py                 # python-docx
      normalize.py            # text → structured profile via LLM
    llm/
      ollama.py               # ChatOllama factory, streaming wrapper, retry
    observability/
      langfuse.py             # optional callback handler (toggle by env)
  frontend/                   # React + TS (Vite + Vitest); see frontend/src/
    src/App.tsx
    src/api.ts                # typed FastAPI client + SSE stream helpers
    src/pages/                # LoginPage / SetupPage / InterviewPage / HistoryPage / ManagePage
    src/state/                # auth + activeJob contexts, multi-tab + auth-expired
    src/components/           # AppShell (left sidebar), DocMappingModal, ActiveJobChip,
                              # ArmedDeleteButton, LoadingStatus, ui primitives
    src/errors.ts             # backend code → user-facing message translation
    src/hooks/useStreamAbort.ts
    src/styles.css            # Copper Aquamarine Dream tokens + button system
  tests/
    unit/
    integration/
```

Dependencies to **add** to `requirements.txt` (the rest is already there):
`sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `python-jose[cryptography]`, `passlib[bcrypt]`, `python-multipart`, `pypdf`, `python-docx`, `langchain-mcp-adapters`, `psycopg[binary]` (for Alembic sync).

---

## Phased build (brick by brick)

Each phase ends with a smoke test the user can run before moving on. The detailed plan for the **active** phase lives at `plan/current-phase.md` (gitignored — overwritten each phase). Past phases are merged into `main` and recorded only here as the running checklist.

**Status legend:** ✅ merged · 🚧 in progress · ⏳ pending

| Phase | Title                                          | Status     |
| ----- | ---------------------------------------------- | ---------- |
| 0     | Skeleton & infra                               | ✅          |
| 1     | Auth + persistence                             | ✅          |
| 2     | Document ingestion (PDF + DOCX)                | ✅          |
| 3     | Job description ingestion                      | ✅          |
| 4     | MCP wiring                                     | ✅          |
| 5     | LLM layer                                      | ✅          |
| 6     | ProfileBuilder + JobAnalyzer agents            | ✅          |
| 7     | CompanyResearcher agent                        | ✅          |
| 8     | QuestionGenerator + streaming                  | ✅          |
| 9     | Evaluator + answer loop                        | ✅          |
| 10    | Supervisor graph                               | ✅          |
| 11    | Observability (Langfuse)                       | ✅          |
| 12a   | Eval harness — question-quality baseline       | ✅          |
| 13    | Variety — deterministic focus picker           | ✅          |
| 13.1  | Interviewer-voice / JD-relevance prompt rework | ⤵ folded into 14.1 |
| 14    | Model-answer RAG grounding (user-doc chunks)   | ✅          |
| 14.1  | Project-identity-aware profile + RAG + prompts | ✅          |
| 16    | Agent layer hardening (telemetry + MCP rework) | ✅          |
| 17    | Embeddings service extraction (sidecar)        | ✅          |
| 18    | UI infra rework (errors, active-job, abort, guards) | ✅      |
| 18b   | UI/UX redesign (Copper Aquamarine, sidebar, wizard) | ✅      |
| 19    | Perf P1 — CPU caps (api/embedder/llama) + ingest sema + ctx/thinking tuning | ✅ |
| 20    | Perf P2 — prompt trimming + retrieval overlap  | ✅          |
| 21    | Perf P3 — prep-graph checkpointer + HITL mapping loop + MCP gut | ✅ |
| 22    | Correctness P4 — CV-replace guard, checkpoint GC, FE drift | ✅ |
| 23    | Cleanup P5 — dead deps (streamlit/litellm/a2a/deepeval), pyproject `ui` | ✅ |
| 24    | Hybrid retrieval — BM25 + vector with RRF      | ✅          |
| 25    | Correctness P6 — setup flow: cache keys, embed-await, prep-in-progress 409, FE node-skip/job-active | ✅ |
| 26    | Arch deepening — prep cache verdict (one Profile-document-set owner + degraded self-heal) | ✅ |
| 14b   | RAG grounding — Tavily tech-spec corpus (opt)  | ⏳          |
| 12b   | Eval harness — evaluator quality (full)        | ⏳          |
| 15    | GitHub ingestion                               | ⏳          |

### Phase 0 — Skeleton & infra
- Convert `requirements.txt` → `pyproject.toml` (uv).
- `docker-compose.yml` with services: `api`, `ui`, `db` (postgres:16). (Tavily MCP deferred to Phase 3.)
- `Dockerfile.api` + `Dockerfile.ui` (uv-based, slim base).
- FastAPI `/healthz` and Streamlit "Hello".
- Pre-commit (ruff, ruff-format), pytest scaffold.
- **Smoke test:** `docker compose up` → `curl :8000/healthz` returns ok, Streamlit page loads.

### Phase 1 — Auth + persistence
- Postgres schema v1: `users(id, email, hashed_password, created_at)`.
- Alembic init + first migration.
- `auth/security.py` — bcrypt hash/verify, JWT issue/decode (HS256, env secret).
- `auth/routes.py` — `POST /auth/register`, `POST /auth/login` (returns access token).
- `auth/deps.py` — `get_current_user` FastAPI dependency.
- Streamlit `login.py` — stores JWT in `st.session_state`; `api_client.py` injects it.
- **Smoke test:** register → login from UI, an authed `/me` endpoint returns the user.

### Phase 2 — Document ingestion (PDF + DOCX)
- Schema add: `documents(id, user_id, kind, filename, raw_text, parsed_json, created_at)` where `kind ∈ {cv, project_doc}`.
- `ingestion/pdf.py` (pypdf), `ingestion/docx.py` (python-docx) — return raw text.
- `documents/routes.py` — `POST /documents` multipart upload, `GET /documents`.
- Streamlit `setup.py` — file upload widget, lists user's docs.
- **Smoke test:** upload a real CV PDF, confirm row in DB with extracted text.

### Phase 3 — Job description ingestion
- Schema add: `jobs(id, user_id, source, raw_text, parsed_json, created_at)`.
- `jobs/routes.py` — `POST /jobs` accepts `{text}` or `{url}`. URL path uses Tavily MCP `extract`.
- Tavily MCP service added to docker-compose here (first phase that actually needs it).
- Pure parsing only here — no LLM analysis yet.
- Setup page in UI gets a JD textarea + URL field.
- **Smoke test:** paste a JD; fetch a JD by URL; rows appear correctly.

### Phase 4 — MCP wiring
- `mcp/servers/documents_server.py` — custom MCP server (stdio, run in api container) exposing tools: `get_user_profile(user_id)`, `get_job(job_id)`, `list_documents(user_id)`, `save_company_snapshot(...)`. These bridge the agent layer to Postgres without giving the agent raw DB access.
- `mcp/client.py` — `MultiServerMCPClient` for `[tavily, documents]`; expose tools to LangGraph via `langchain-mcp-adapters`.
- **Smoke test:** standalone script lists tools from both MCP servers and invokes one.

### Phase 5 — LLM layer
- `llm/ollama.py` — `ChatOllama` factory pinned to `qwen3:8b`, base URL from env, `temperature` per-call, tenacity retry on connection errors.
- Streaming helper that yields tokens for SSE consumption.
- **Smoke test:** unit test hits the host Ollama and prints a streamed response.

### Phase 6 — First two agents: ProfileBuilder + JobAnalyzer
- `agents/state.py` — `InterviewState` TypedDict: `user_id`, `session_id`, `round_type`, `profile`, `job`, `company`, `current_question`, `current_answer`, `evaluation`, `turn_index`, `next_step`, `messages`.
- `agents/nodes/profile_builder.py` — pulls user docs via MCP, asks LLM to extract a structured profile (skills, projects, experiences). Stored in `profiles` table (new).
- `agents/nodes/job_analyzer.py` — pulls JD via MCP, structures it (title, level, must-haves, nice-to-haves, signals). Stored in `jobs.parsed_json`.
- Both nodes are pure functions of state + tools; testable in isolation.
- **Smoke test:** integration test runs the two nodes against a fixture CV + JD, asserts non-empty structured outputs.

### Phase 7 — CompanyResearcher agent
- `agents/nodes/company_researcher.py` — Tavily MCP search + extract for company name parsed in JD analyzer; LLM compresses into a snapshot (mission, products, recent news, values, interview signal).
- New table `company_snapshots(id, job_id, snapshot_json, created_at)`.
- Cache-aware: if a snapshot for this job exists, reuse it.
- **Smoke test:** kick off research for a known company, assert snapshot has the four sections.

### Phase 8 — QuestionGenerator + streaming endpoint
- `agents/nodes/question_generator.py` — inputs: profile, parsed JD, company snapshot, round_type, prior turns; output: one question + an `evaluation_anchors` list (used later by evaluator).
- Two distinct prompt templates per round type:
  - **resume_walkthrough** — drills into a specific bullet/project from the profile.
  - **behavioral_star** — asks a STAR-shaped behavioral question rooted in the JD's competency signals.
- `sessions/routes.py` — `POST /sessions` to start a session, `POST /sessions/{id}/next_question` returns SSE stream.
- Streamlit `interview.py` consumes the SSE with `st.write_stream`.
- **Smoke test:** start a session and watch a personalized question stream into the UI.

### Phase 9 — Evaluator + answer loop
- Schema add: `sessions(id, user_id, round_type, status, created_at)`, `turns(id, session_id, question, answer, score, feedback, model_answer, anchors_json, created_at)`.
- `agents/nodes/evaluator.py` — single 1–10 score + concise feedback paragraph + a "model answer" written in candidate's voice. Uses `evaluation_anchors` from the question.
- `POST /sessions/{id}/answer` — accepts answer, streams evaluator output (score arrives first as a JSON event, then feedback tokens, then model answer tokens).
- UI: chat-style; after evaluation the user clicks "Next question" → loops back to phase 8 endpoint.
- Configurable `n_questions` per session (default 5); session marked `complete` when reached.
- **Smoke test:** complete a 5-question round end to end; rows in `turns` populated; can replay from history page.

### Phase 10 — Supervisor graph
- `agents/graph.py` — `StateGraph` with supervisor that routes by `state.next_step`:
  `START → profile_builder → job_analyzer → company_researcher → question_generator → (await answer) → evaluator → (loop or END)`.
- Checkpointer: SQLite file volume (`langgraph-checkpoint-sqlite`).
- API routes call `graph.ainvoke` / `graph.astream` with `thread_id = session_id`.
- Resumability: an interrupted session can resume from last checkpoint.
- **Smoke test:** kill the api container mid-session, restart, resume from where you left off.

### Phase 11 — Observability
- `observability/langfuse.py` — `LangfuseCallbackHandler` wired into LangGraph runs when `LANGFUSE_PUBLIC_KEY` env is set (no-op otherwise).
- Tag traces with `user_id`, `session_id`, `round_type`, `node`.
- **Smoke test:** complete a session with Langfuse env set; trace tree visible in dashboard.

### Phase 12a — Eval harness (question-quality baseline)
The original Phase 12 was split: 12a lands a **thin** harness *before* any
quality-improvement work, so Phases 13/14 have an objective baseline to
move. The full evaluator-quality eval is now Phase 12b, after RAG.
- `tests/integration/eval/test_question_quality.py` — 10 (profile, JD, raw_cv) fixtures; for each, generates 5 questions and computes 3 metrics:
  - **distinctness** — mean pairwise cosine distance between same-session questions (variety signal).
  - **profile groundedness** — G-Eval over (question, profile_json + raw_cv) (RAG signal).
  - **JD relevance** — G-Eval over (question, job_analysis).
- `tests/integration/eval/report.py` — prints a `metric × phase` comparison table; appends to a CSV that 13/14/14b refresh.
- Soft thresholds (informational, non-failing) in 12a; later phases assert deltas.
- **Smoke test:** `pytest tests/integration -k quality` runs and prints baseline numbers.

### Phase 13 — Variety: deterministic focus picker
- Pre-pick the focus *before* the LLM sees the prompt; remove the LLM's freedom to keep returning to the same prominent bullets.
- `agents/nodes/question_generator.py`:
  - `_pick_focus_target()` — for `resume_walkthrough`, build candidates from `profile.experiences` + `profile.projects`; score each by inverse-frequency over the user's prior `turns.metadata_json.focus_key` *for this `(user_id, job_id)`* and JD-skill overlap; weighted-sample.
  - For `behavioral_star`: replace `random.choice` over signals with the same inverse-frequency picker.
  - Persist the chosen `focus_key` into `turns.metadata_json` so subsequent picks see history.
- `db/repos.py` — new `list_prior_questions_for_user_job(user_id, job_id, limit=30)` and `count_focus_keys(user_id, job_id)`. Cross-session prior-question dedup replaces the per-session `prior_turns` field.
- `agents/prompts.py` — extend resume + behavioral system prompts with `focus_target` constraint language ("drill into this; do not pick a different topic").
- **No schema change** — `turns.metadata_json` already exists.
- **Smoke test:** two 5-question sessions on the same (user, JD); union of `focus_key`s ≥ 6 distinct values; rerun 12a harness — distinctness metric improves measurably; groundedness holds.

### Phase 13.1 — Interviewer-voice / JD-relevance prompt rework
- Pure prompt rework on `agents/prompts.py` + a small reshape of the question-generator user-message JSON. No infra, no schema, no retrieval.
- System prompts become small templates rendering `{company_name}`, `{role_title}`, `{seniority}`, `{mission}`, `{values_and_signals}` into a "You are a hiring manager at $COMPANY for $ROLE…" preamble.
- Add an explicit "phrase the question in second person; reference the role's responsibility or the company's domain when natural" instruction.
- For `resume_walkthrough`: connect `focus_target` to one of the role's `must_have_skills` or `responsibilities`. For `behavioral_star`: tie the competency back to the company's stated values when present.
- `agents/nodes/question_generator.py` — reshape user-message JSON to `{focus_target, role: {...}, company: {...}, profile, prior_turns}`, promoting role+company up the attention hierarchy.
- **Smoke test:** rerun 12a harness — `jd_relevance` recovers; `profile_groundedness` and `distinctness` hold within a small delta.

### Phase 14 — Model-answer RAG grounding (user-doc corpus)
- Information asymmetry: the **interviewer** only knows the resume / Profile JSON, while the **candidate** knows their own deeper write-ups. Phase 14 wires those write-ups into the **evaluator's model-answer call only**, so the reference answer can speak with project-specific detail in the candidate's first-person voice. The question generator stays untouched; question-side grounding lands in 14b (Tavily tech specs) and 15 (GitHub).
- New table `grounding_chunks(id, user_id, document_id, source_doc_kind, chunk_index, text, n_tokens, embedding vector(1024), model_name, created_at)` (pgvector). `source_doc_kind` is a free-form `varchar(32)` with a check constraint listing `{cv, project_doc}` today; Phase 15 widens the constraint to add `'github'` (no schema migration). `hnsw` index on `embedding` for cosine ANN.
- `rag/embeddings.py` — lazy `jinaai/jina-embeddings-v3` singleton via `sentence-transformers` (`trust_remote_code=True`); `rag/chunking.py` — pure-text 400-token windows with 50-token overlap (the safe path; late-chunking is an optimization target).
- `rag/ingest.py` — `embed_and_store_document(document_id)` is idempotent (delete-then-insert). Wired as a fire-and-forget background task in `documents/routes.py` so the upload response stays snappy. One-shot `scripts/backfill_grounding.py` for pre-existing dev DBs.
- **Evaluator split into two sequential LLM calls** (single GPU, qwen3:8b VRAM-bound — parallelism would queue or spill to CPU):
  - **Judge call** — `EVALUATOR_JUDGE_SYSTEM`, emits `{score, feedback}`, NO grounding injected.
  - **Model-answer call** — `MODEL_ANSWER_SYSTEM`, emits `{model_answer}`, with retrieval over `('project_doc',)` chunks injected. Voice-contamination guard in the prompt: never quote the documents verbatim; never cite ("as stated in my notes"); render specifics in natural first-person speech.
  - Wire format unchanged: `score → feedback_token* → feedback_done → model_answer_token* → model_answer_done → done`. New `model_answer_error` event covers the partial-failure path (judge succeeded, model-answer flaked); `repos.update_turn_evaluation_partial` persists score+feedback only.
- `question_generator.py` — one-line addition: persist `focus_label` alongside existing `focus_key` in `turns.metadata_json` so the evaluator can use it as part of the retrieval query.
- `mcp/servers/documents_server.py` — bonus `search_grounding(user_id, query, k, source_kind=None)` tool.
- `tests/integration/eval/test_model_answer_quality.py` + `model_answer_faithfulness` G-Eval (informational, no failing threshold) — Phase 14 baselines only.
- Compose: switch base image `postgres:16` → `pgvector/pgvector:pg16`.
- **Smoke test:** upload a real CV + project_doc; `grounding_chunks` populates within ~5s/doc; run a session and inspect a model_answer for first-person voice + grounded specifics + no document-style citation; eval baseline numbers print.

### Phase 16 — Agent layer hardening (telemetry + structured retry + MCP rework)
- **LLM telemetry**: new `llm_calls(id, ts, node_name, model, prompt_tokens, completion_tokens, latency_ms, retry_count, success, error_class)` table (Alembic `0009`). `llm/telemetry.py` provides `set_node_context(name)` (ContextVar-based, async-safe) and `record_call(...)`; `llm/client.py` wraps both call shapes (`ainvoke_with_telemetry`, `astream_with_telemetry`, plus telemetry-aware `stream_text`). Token counts captured from any chunk carrying `usage_metadata` — llama.cpp emits it on a trailing chunk after the final content delta, so naive "track last chunk" loses the row.
- **Structured-output self-correction**: `chat_model_structured[T: BaseModel](schema, ...)` wraps `with_structured_output(schema, method="json_schema", include_raw=True)`. On `ValidationError | OutputParserException | ValueError` the call retries once with a `HumanMessage` explaining the failure; `retry_count=1` is recorded in telemetry. `include_raw=True` keeps `usage_metadata` reachable for token accounting.
- **MCP rework**: new `providers/` package (`base.py` Protocols, `tavily.py`, `registry.py`) is the actual swap-able seam — MCP servers are now thin shells over it. New `web_server` exposes `web_search` + `web_fetch` tools (deferred-import path keeps subprocess startup cheap). `documents_server` slimmed to `get_job` + `search_grounding` tools, plus a `project_doc://{user_id}/{document_id}` Resource (CV intentionally not exposed). `ingestion/web.py` kept as a one-release re-export shim for `jobs/routes.py`. Boundary rules (MCP wraps external-world I/O and LLM-readable surfaces only; never app-owned Postgres CRUD; thin shells over providers; two callers — internal-direct and MCP-wrapped — is correct) pinned for future phases.
- **Smoke test:** end-to-end session populates `llm_calls` rows for all six call sites (profile_builder, job_analyzer, doc_intake, company_researcher, question_generator, evaluator_judge, evaluator_model_answer) with non-zero `prompt_tokens`/`completion_tokens`/`latency_ms`; `ps -ef | grep python` inside the api container shows `documents_server` + `web_server` subprocesses; injected malformed structured output records `retry_count=1`.

### Phase 17 — Embeddings service extraction (sidecar)
- Embeddings move out of the `api` container into a new `embedder` FastAPI sidecar. `api` drops `sentence-transformers` + `torch` from its install closure and talks to the sidecar over HTTP via a small `EmbeddingClient`. Single `POST /embed` endpoint takes `{texts, task}` (where `task` ∈ `retrieval.passage | retrieval.query`); `GET /model` exposes `{name, dim}` and api asserts the lock on lifespan boot. Weights persist via a mounted HF cache volume.
- **Why a custom thin wrapper, not TEI/Infinity**: TEI does not natively support `jinaai/jina-embeddings-v3` (missing `model_type` field; only unofficial converted forks). Infinity lists the model but does not clearly expose task-specific LoRA adapter selection. Numeric parity is non-negotiable since existing `grounding_chunks` rows are tagged with `model_name = "jinaai/jina-embeddings-v3"`.
- Chunking stays on api via a tokenizer-only path (`AutoTokenizer.from_pretrained("jinaai/jina-embeddings-v3", trust_remote_code=True)`). MCP `documents_server` subprocess builds its own `EmbeddingClient` from env (no `app.state` available).
- **Smoke test:** `make up` brings up both services; `curl :8001/model` returns the locked name+dim; end-to-end session works; parity test shows `||Δ||_inf < 1e-6` between in-process and over-the-wire vectors; api image is materially smaller; killing `embedder` mid-flight degrades gracefully (ingest errors, retrieval returns `[]`).

### Phase 18 — UI infra rework (errors, active-job, abort, guards)
Backend + frontend infrastructure layer for a usable multi-JD UX, without touching
the visual design. Four commits land together.
- **Backend**: CV upload auto-schedules `profile_builder` as a second background
  task (mirrors the existing embedding task pattern); new
  `POST /documents/{cv_id}/rebuild-profile` (202, idempotent, single-flight via
  module-level `set[uuid.UUID]`); `GET /documents` and `GET /documents/{id}` gain
  an `embedding_status: "ready" | "pending" | "failed" | "n_a"` field derived
  from `count(grounding_chunks)` + doc age (60s grace); `DELETE /jobs/{id}`
  returns 409 `job_in_use` if any active session references it;
  `DELETE /documents/{cv_id}` returns 409 `cv_in_use` if any active session
  exists.
- **Frontend infra**: `errors.ts` central code → user-message translator
  consumed by a new `<ErrorBanner code={…} />` component (no more raw
  `ProfileMissing` / `NoSearchHits` leaking to users); `state/activeJob.tsx`
  Context persisted via `localStorage`, validated on mount with silent
  404-fallback to the most-recent JD; `state/auth.tsx` gains a `storage`
  event listener for multi-tab logout + a global `auth-expired` event from
  `apiFetch`'s 401 path; `api.ts` `streamPost` accepts an optional
  `AbortSignal` and `useStreamAbort` manages one per page;
  `ArmedDeleteButton` for two-click destructive confirms.
- The dead Streamlit `ui/` directory was deleted in commit 1; `Dockerfile.ui`
  already built from `frontend/`.
- **Smoke test:** end-to-end demo as before, plus: upload a CV → embedding
  pill flips ready within ~15s and profile_ready becomes true without a
  manual prep run; uploading a project_doc pre-profile shows a graceful
  "Profile is still building" panel and auto-promotes on profile-ready;
  deleting a JD with an active session returns 409 with a translated
  `job_in_use` message; expired JWT auto-redirects to `/login`; multi-tab
  logout propagates within the storage tick.

### Phase 18b — UI/UX redesign (Copper Aquamarine, sidebar, wizard)
Visual + IA rework on top of Phase 18. No new backend contract beyond one
small change. Reference designs: Raycast (surface ladder, hairline borders),
Arc (warm dark base), Perplexity (answer-focused practice surface).
- **Design tokens**: Copper Aquamarine Dream palette (Figma library) extracted
  to a dark base. Copper `#C56B62` primary, peach `#DEA785` hover, slate-blue
  `#6C739C` for info/ready status (replaces the prior sage green that
  clashed), taupe `#BFB9B5` for secondary text. New surface ladder
  `#0d0d11 → #15151b → #1d1d24 → #27272f`. Generous letter-spacing,
  Inter/Geist stack.
- **Left-sidebar shell**: top nav and big-tab cards removed. 220px sidebar
  with brand at top, nav middle, active-job pill + account menu at the
  footer. Below 800px the sidebar flattens to a horizontal strip.
- **Active-job pill**: full-width sidebar pill showing role on line 1 and
  company on line 2; opens an upward dropdown listing other JDs; when the
  user has zero jobs the pill becomes a direct "Go to Setup" affordance
  instead of opening an empty menu.
- **Button system**: `.btn-primary` / `.btn-secondary` / `.btn-ghost`
  / `.btn-quiet` semantic classes. Old `.primary-button` / `.ghost-button`
  rules retokened in place so phase-18 page contents render correctly under
  the new system without a per-page rewrite.
- **Setup as a wizard**: SetupPage restructured into 4 explicit steps —
  CV upload → JD paste → optional supporting docs → run prep. Returning
  users with a complete setup see a "Ready to practice" landing card with
  CV filename, JD snippet, and supporting-doc count instead of the wizard.
  "Add another job" and "Add supporting doc" buttons on the landing route
  back into the wizard at the right step (via an `overrideReady` flag so
  the auto-skip doesn't bounce them back out). A separate `/setup/manage`
  page hosts destructive actions and full lists.
- **Practice focus mode**: bare `<select>` job picker removed; current
  question rendered at 28–32px display type; answer textarea wide but
  bounded; "End session" demoted to `.btn-quiet` at the bottom (still
  armed-delete). After answer submission the page parks on the feedback +
  model answer indefinitely; an explicit "Next question →" / "Finish
  round →" CTA is the only thing that advances state.
- **History timeline**: grouped by job_id with `{role} @ {company}` headers
  from cached `getJob` calls.
- **Backend addition**: `repos.delete_profile(user_id)` called from the
  CV delete path so stale `profile_ready=true` doesn't survive a CV
  deletion. Covered by `test_delete_cv_drops_profile`.
- **Polish**: success toasts auto-dismiss after 3.5s and clear on step
  change (no more "Job description saved." sticking through every wizard
  step); prep-time copy updated from misleading "~20–40s" to "can take a
  minute or two"; JD list items show source URL hostname or a preview
  snippet rather than "Pasted JD".
- **Smoke test:** full first-time flow on a fresh user lands cleanly on the
  practice surface; "Add another job" reaches step 2 from a setup-complete
  state; deleting a CV clears the profile so the wizard re-asks for one;
  answering a question parks on the feedback until the user clicks the
  advance CTA; one-job dropdown no longer shows a phantom scrollbar.

### Phase 19 — Perf P1: thinking-mode off + CPU contention

First phase of a five-phase performance + cleanup track, ordered smallest-risk
first. Stops the two daily-felt pains: qwen3 burning tokens on `<think>` blocks
on every call, and the api + embedder collectively spiking host CPU during CV
upload so other apps choke.

- **Thinking off everywhere** — `llm/client.py` `chat_model()` injects
  `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` by default.
  Verified zero references to thinking config anywhere in the repo today, so
  every LLM call currently pays the thinking tax (Qwen3 default-on).
- **CPU caps for the api container** — add `OMP_NUM_THREADS=2`,
  `MKL_NUM_THREADS=2`, `TOKENIZERS_PARALLELISM=false` to the api env in
  `docker-compose.yml`. Cap embedder host CPU with a new `cpus:` quota
  (default 4) and drop `EMBED_THREADS` default to 4.
- **Bounded background concurrency** — module-level `asyncio.Semaphore(1)`
  shared by `_embed_in_background` + `_profile_build_in_background` in
  `api/documents/routes.py`, so the two CV-upload background tasks run
  sequentially not in parallel. The Sema is held inside each wrapper, the
  outer `asyncio.create_task` call shape is unchanged.
- **Symmetric apply_mapping** — swap the inline `await
  embed_and_store_document` in `agents/nodes/doc_intake.py:apply_mapping` for
  `asyncio.create_task(_embed_in_background(document_id))` using the same
  wrapper as the CV path. The UI already drives off `embedding_status`
  polling, so no FE change.
- **Smoke test:** end-to-end session post-fixes; `llm_calls` rows show
  materially lower `completion_tokens` per call (target: ≥30% drop on the
  question_generator and model_answer calls); per-turn wall time falls by
  several seconds; uploading a CV during a host build does not pin CPU.

### Phase 20 — Perf P2: prompt trimming + retrieval overlap

Latency lever that's visible per question. Higher semantic risk than 19, so
lands after P1's measurements.

- **Focused profile in prompts** — `question_generator` and `evaluator`
  payloads carry only the picked focus's experience/project + a compact index
  of the rest, not the full profile JSON. The deterministic focus picker
  already knows the target.
- **Hydrate InterviewState once** — `next_question` populates
  `state["profile"]`, `state["job"]`, `state["company"]` from a single
  Postgres read at turn 0, then subsequent turns reuse them. Drops 3 of the 5
  per-turn round trips.
- **Overlap retrieval with judge call** — in `evaluator.stream_evaluation`,
  kick off `_retrieve_for_turn` as an `asyncio.create_task` immediately before
  the judge call, then `await` it just before constructing the model-answer
  message. They use disjoint resources (GPU vs embedder + pgvector).
- **Smoke test:** run a session, inspect `llm_calls.prompt_tokens` — should
  fall noticeably on `question_generator` and `evaluator_judge`. Per-question
  TTFT should improve. Manual eval check: questions still feel grounded in
  the candidate's profile.

### Phase 21 — Perf P3: prep-graph checkpointer + mapping fold-in

Implements the structural change to setup flow. Lands after 19+20 so the
perf wins are in place before we add a new node.

- **Checkpointer on prep_graph** — reuse the same `AsyncSqliteSaver` opened
  in `api/main.py:lifespan`. `thread_id = "prep:{user_id}:{job_id}"` —
  well-defined because the JD row exists before `prep_graph` is ever invoked
  (CV upload runs profile-build as a standalone task, not via the graph).
  Killing api mid-prep and restarting resumes from the last completed node.
- **Project_doc intake folded into prep_graph** — a new `doc_intake_fanout`
  node runs after `profile_builder`, calls `run_intake` (LLM) in parallel for
  each unmapped project_doc the user has, and emits `mapping_suggestion`
  SSE events. **HITL preserved**: the modal still opens for user confirmation,
  but the suggestion is precomputed during prep so the modal opens instantly.
- **Fix profile cache key (G4)** — `apply_mapping` updates the profile's
  `source_doc_ids` to include the project_doc id. Without this, the
  `profile_builder` cache check in `graph_nodes.py:71-77` always misses after
  the first project_doc upload — re-running a full LLM call on every prep.
  Silent regression today.
- **Drop MCP `get_job` for direct Postgres reads** — `job_analyzer` and
  `company_researcher` call `repos.get_job` directly instead of going through
  the MCP `get_job` tool. Per CLAUDE.md boundary rule, MCP wraps external-
  world I/O, not app-owned CRUD. Cuts a subprocess JSON-encode/decode hop
  per prep run.
- **Slim `/sessions/prepare/status` payload** — drop the `profile`/`job`/
  `company` fields from the response unless `?detail=true`. SetupPage polls
  every 2s and never uses them. Raise the poll interval to 4s in
  `SetupPage.tsx` since profile_builder takes 10–30s anyway.
- **CV re-embedding affordance** — `/documents/{cv_id}/rebuild-profile` also
  re-embeds (idempotent: `embed_and_store_document` already deletes prior
  chunks). Today only the profile is rebuilt — a CV with
  `embedding_status='failed'` has no UI affordance.
- **Smoke test:** kill api mid-prep, restart, reopen Setup → prep resumes
  from the last completed node. Upload a project_doc → mapping modal opens
  populated (no extra LLM wait). Re-run prep after a project_doc upload →
  `profile_builder` correctly skipped (`node_skipped: cached`).

### Phase 22 — Correctness P4: CV-replace guard, checkpoint GC, FE drift

Bag of one-line correctness fixes uncovered during the audit. Small footprint
across many files.

- **Block CV replace mid-session (G3)** — `upload_document` for `kind='cv'`
  refuses with 409 `cv_in_use` if the user has active sessions, matching the
  DELETE guard.
- **Garbage-collect graph checkpoints (J4)** — a lifespan startup pass (or
  background tick) drops checkpoints whose session is `complete`/`abandoned`
  and older than 7 days, via `AsyncSqliteSaver.adelete_thread`.
- **Fix `submitJobText` empty-error code (E2)** — replace
  `setError("empty_answer")` with a JD-specific code in `errors.ts`.
- **Drop unused turn fields (E4)** — stop returning `anchors_json` /
  `metadata_json` from `GET /sessions/{id}` unless we surface them in the UI.
  Defer the surfacing decision; for now, trim the wire payload.
- **Fix `activeJob` localStorage clearing (E7)** — call the public
  `setActiveJobId(null)` in `state/activeJob.tsx:104-107` instead of the
  state setter directly, so localStorage stays in sync.
- **Smoke test:** unit-test the 409. Manual verify rest.

### Phase 23 — Cleanup P5: dead deps + dead config

Pure hygiene — zero behavior change, shrinks api image by ~200MB.

- **Remove `ui` from `pyproject.toml`** — both `[tool.hatch.build.targets.
  wheel].packages` and `[tool.ruff].src`. The `ui/` directory was deleted in
  Phase 18 but `pyproject.toml` still references it.
- **`uv remove streamlit`** — Phase 18 deleted Streamlit; the dep lingers,
  pulling tons of transitive baggage into the api image.
- **`uv remove a2a-sdk`** — reserved post-v1, never imported. Re-add when
  the A2A wrapping phase actually starts.
- **`uv remove litellm`** — we use `langchain-openai` exclusively, no
  litellm import anywhere.
- **Move `deepeval` to `[dependency-groups].dev`** — only used by the
  integration eval harness, not by runtime code.
- **Smoke test:** `make up`, full session works. `docker images
  interview_coach-api` shrinks materially.

### Phase 14b — RAG grounding (Tavily tech-spec corpus, optional)
Only if Phase 14 questions feel grounded in the candidate's voice but still technically vague.
- New `tech_corpus_ingester` node in `prep_graph`, between `job_analyzer` and `company_researcher`.
- Reads `job.parsed_json.must_have_skills`, picks top 5, runs Tavily search + extract for ~3 authoritative pages per skill.
- Same chunker / embedder as Phase 14; rows inserted with `corpus_kind='tech_spec'`, scoped by `job_id` (not `user_id`).
- Retrieval fetches top-3 user-attested + top-2 spec-attested separately; prompt distinguishes them ("user-attested = anchor your question here; spec-attested = sharpen technical specificity").
- **Smoke test:** rerun 12a harness — JD-relevance improves, groundedness holds.

### Phase 12b — Eval harness (evaluator quality, full)
The rest of the original Phase 12. Lands after RAG so the eval set is stable.
- `tests/integration/eval/test_evaluator_quality.py` — fixture (question, good/mediocre/bad answer) triples; assert score ordering.
- Feedback faithfulness G-Eval — does feedback reference anchors actually present in the question's `anchors_json`?
- model_answer faithfulness G-Eval — does `model_answer` use details from profile / grounding?
- **Smoke test:** `pytest tests/integration -k quality` runs all metrics and reports.

### Phase 15 — GitHub ingestion
- `documents.kind = 'github_repo'`; resolve handles from CV (regex on raw_text + `links` if present).
- GitHub REST API (no auth needed for public repos) → README + top-level `.md` + `package.json` / `pyproject.toml`.
- Reuse Phase 14 chunker / embedder / table — adding GitHub is a one-line widening of the `source_doc_kind` check constraint to include `'github'` plus changing the `retrieve_grounding` default `source_kinds` to `('project_doc', 'github')`. No new migration.
- **Smoke test:** ingest a known repo; chunks searchable; model_answer references attested code patterns.

---

## Out of scope for v1 (post-v1 backlog, in priority order)

1. A2A wrapping of each specialized agent (`a2a-sdk` already in deps).
2. STT (faster-whisper container or browser Web Speech API).
3. ~~GitHub URL ingestion~~ — promoted to Phase 15.
4. Markdown / plain text doc ingestion.
5. Technical / coding round (with code-execution sandbox) and System-design round.
6. Multi-dimensional rubric (Correctness / Depth / Clarity / Structure).
7. CrewAI experiment (it's in deps; revisit only if LangGraph topology gets unwieldy).

---

## Critical files to create / modify

- `pyproject.toml` (new — convert from `requirements.txt`)
- `docker-compose.yml`, `Dockerfile.api`, `Dockerfile.ui`, `.env.example`
- `alembic.ini` + `alembic/` (new)
- `src/interview_coach/api/main.py` — FastAPI app, lifespan boots MCP client + LangGraph
- `src/interview_coach/agents/graph.py` — supervisor StateGraph; central piece
- `src/interview_coach/agents/state.py` — single source of truth for graph state
- `src/interview_coach/agents/nodes/*.py` — one file per agent
- `src/interview_coach/mcp/servers/documents_server.py` — custom MCP server
- `src/interview_coach/db/models.py` — SQLAlchemy schema (incremental per phase)
- `src/interview_coach/llm/ollama.py` — ChatOllama factory (used by every node)
- `frontend/src/pages/InterviewPage.tsx` — streaming chat loop (SSE with AbortController)
- `frontend/src/styles.css` — design tokens + sidebar shell + button system

---

## Reuse from existing dependencies (no need to reinvent)

- `langgraph-checkpoint-sqlite==3.0.3` → graph checkpointer (phase 10).
- `langchain-ollama==1.0.0` → `ChatOllama` (phase 5).
- `mcp==1.26.0` → server SDK for `documents_server.py` (phase 4).
- `langfuse==4.0.1` → callback handler (phase 11).
- `deepeval==3.9.1` → eval metrics in `tests/integration` (phase 12).
- `tenacity` → retry around Ollama and Tavily calls.
- `pydantic==2.11.9` → all request/response models and structured LLM outputs (`with_structured_output`).

---

## Verification — end-to-end demo path

After phase 9 is green (the earliest "real" demo):

1. `docker compose up -d` → wait for healthy.
2. Browse to `http://localhost:8501` (the React app), register a user, log in.
3. Upload a real CV PDF + one project doc (DOCX).
4. Paste a JD URL; system fetches and parses it.
5. Pick round type **Resume Walkthrough**; click **Start interview**.
6. Watch the first question stream in; type an answer.
7. Submit → score (1–10), feedback, and model answer stream in.
8. Click **Next question**; repeat 5x.
9. Visit **History** page; confirm session + turns persisted.
10. Restart the api container mid-session (after phase 10) → reopen session → it resumes.

Observability (after phase 11): every step above shows up as a trace in Langfuse keyed to the session.

Eval (after phase 12): `pytest tests/integration -k quality` passes deepeval thresholds.

---

## Notable risks / things to watch

- **qwen3:8b latency** on CPU-only hosts may make per-turn evaluation slow; streaming masks it but consider lowering `n_questions` default or warning users. Keep a `MODEL_NAME` env so we can swap to a smaller model for testing.
- **Tool-binding in `langchain-ollama` 1.0.0**: confirm tool-call support against qwen3:8b at the start of phase 6; if flaky, fall back to manual JSON-mode prompting.
- **MCP server lifecycle inside api container**: prefer in-process `MultiServerMCPClient` boot over subprocess management for the custom `documents_server`.
- **SSE streaming on the React side**: handled by `frontend/src/api.ts` `streamPost` which accepts an `AbortSignal`; pages create one per stream via `useStreamAbort` and abort on unmount. Mid-stream HTTP-side disconnects are converted to a synthetic `("error", {code: "stream_interrupted"})` frame instead of throwing.
- **Postgres + Alembic in compose**: ensure api waits for DB readiness (healthcheck + `depends_on: condition: service_healthy`).

---

## Workflow (read this if you're a coding agent picking this up)

This project is built **brick by brick, one phase at a time**. Do not try to implement multiple phases in one go. The owner is in the loop at every gate.

For each phase:

1. **Branch** — create `phase-N-<slug>` off `main` (e.g., `phase-3-job-ingestion`).
2. **Detailed plan** — write the phase's full plan to `plan/current-phase.md` (this file is gitignored on purpose; it gets overwritten each phase). Include:
   - Goal and scope (locked from the master plan above)
   - Open decisions for the user with recommendations
   - Exact files to create/modify
   - Schema changes (if any)
   - API contract
   - Smoke test (the **definition of done**)
   - Out-of-scope items deferred to later phases
   - Risks
3. **Wait for user approval** of the plan and any open decisions. Do not start coding until the user says go.
4. **Implement** on the branch. Use `TodoWrite` to track sub-tasks.
5. **Self-test** — run the smoke test from `plan/current-phase.md` end-to-end. Pytest must pass; lint + format must pass. Bring the stack down before handoff.
6. **Hand off to the user** — they will run the same smoke test on their side. Do not merge.
7. **On user approval**, fast-forward `main` and `git push origin main`.
8. **Update `plan/master.md`** — flip the phase status from ⏳ → ✅ in the table.
9. **Move on** to the next phase only after the merge.

If the user says "merge" or "approve" or "looks good move on" — do steps 7, 8, then start the next phase's branch + draft `plan/current-phase.md`.

If the user has feedback on a finished phase, fix on the same branch with a follow-up commit before merging.

### Conventions
- Default to user-recommended options unless the user picks otherwise. The user often answers "yes to all recommends".
- Prefer SQLAlchemy 2 typed ORM, async sessions, Pydantic v2.
- Migrations: Alembic, run automatically on `api` container startup via `entrypoint.sh`.
- Tests: in-memory `sqlite+aiosqlite` for unit/auth/api tests via `httpx.ASGITransport`. Live Postgres only for the smoke test.
- Lint: `ruff check` + `ruff format --check` clean before commit.
- Commits: descriptive multi-line messages with Co-Authored-By footer.

### File map (this evolves; check the actual repo for current state)

```
plan/master.md            ← this file (committed)
plan/current-phase.md     ← active phase detail (GITIGNORED, overwrite per phase)
src/interview_coach/      ← FastAPI app, db, ingestion, agents (later), llm (later)
ui/                       ← Streamlit (app.py + pages/)
alembic/                  ← migrations
tests/                    ← pytest
docker-compose.yml        ← db (postgres:16) + api + ui ; Ollama on host
Dockerfile.api            ← runs alembic upgrade head before uvicorn
```
