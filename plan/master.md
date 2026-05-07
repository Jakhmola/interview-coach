# Interview Coach — Master Plan

## Context

Greenfield project (only `requirements.txt` + `uv.lock` exist) to build a webapp that helps a candidate prepare for a specific job. Flow: candidate uploads CV/project docs, supplies a job description (raw or URL), the system researches the company, then runs a per-round interview where it asks personalized questions, scores answers, gives feedback, and shows a model answer. v1 targets two round types — **Resume / Project Deep-Dive** and **Behavioral / STAR**.

The stack is fixed by user choice and the existing `requirements.txt`:
FastAPI + Streamlit + Postgres + LangGraph (multi-agent supervisor) + LangChain + MCP (Tavily for web, custom server for our own tools) + Ollama on host (qwen3:8b) + Docker Compose. Multi-user, JWT + bcrypt auth. Streaming responses. A2A wrapping deferred to phase 2. STT, GitHub ingestion, Markdown ingestion, technical/system-design rounds — all out of v1.

The plan is organized as bricks: each phase is independently testable end-to-end before the next is started.

---

## High-level architecture

```
┌─ Streamlit UI (container) ──────────────┐        Ollama on host
│  pages: login / setup / interview /     │        host.docker.internal:11434
│  history; api_client → FastAPI          │                ▲
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
  ui/
    app.py                    # Streamlit entry
    pages/{login,setup,interview,history}.py
    api_client.py             # httpx client → FastAPI
    state.py                  # st.session_state helpers
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

| Phase | Title                                | Status     |
| ----- | ------------------------------------ | ---------- |
| 0     | Skeleton & infra                     | ✅          |
| 1     | Auth + persistence                   | ✅          |
| 2     | Document ingestion (PDF + DOCX)      | ✅          |
| 3     | Job description ingestion            | ✅          |
| 4     | MCP wiring                           | ✅          |
| 5     | LLM layer                            | ✅          |
| 6     | ProfileBuilder + JobAnalyzer agents  | ✅          |
| 7     | CompanyResearcher agent              | ✅          |
| 8     | QuestionGenerator + streaming        | ✅          |
| 9     | Evaluator + answer loop              | ✅          |
| 10    | Supervisor graph                     | ✅          |
| 11    | Observability (Langfuse)             | 🚧          |
| 12    | Eval harness (deepeval)              | ⏳          |

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

### Phase 12 — Eval harness (deepeval)
- `tests/integration/test_evaluator_quality.py` — fixture set of (question, good_answer, bad_answer); deepeval metrics (G-Eval) assert score ordering and feedback faithfulness.
- `tests/integration/test_question_quality.py` — assert generated questions are grounded in profile + JD (faithfulness metric).
- **Smoke test:** `pytest tests/integration -k quality` runs and reports.

---

## Out of scope for v1 (post-v1 backlog, in priority order)

1. A2A wrapping of each specialized agent (`a2a-sdk` already in deps).
2. STT (faster-whisper container or browser Web Speech API).
3. GitHub URL ingestion (clone or REST API → README + structure as project doc).
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
- `ui/pages/interview.py` — streaming chat loop

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
2. Browse Streamlit, register a user, log in.
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
- **Streaming through SSE + Streamlit**: works, but `httpx` async streaming + `st.write_stream` needs a small adapter — write that helper once in `ui/api_client.py`.
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
