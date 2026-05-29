# Interview Coach — Master Plan

> **Note (Phase 18):** the user-facing UI is now a React + TypeScript app under `frontend/` (built by `Dockerfile.ui`). The old Streamlit `ui/` directory was removed in Phase 18. Mentions of Streamlit, `ui/pages/*.py`, and `st.*` in earlier phase descriptions below are historical — see `frontend/src/` for the live UI. Likewise, early phases say **Ollama** / `ChatOllama` / `llm/ollama.py`; the LLM backend is now a llama.cpp CUDA container (`llama`) reached via `langchain-openai`'s `ChatOpenAI` in `llm/client.py`. And the **MCP** layer (Phases 4/16/17 — `mcp/`, `documents_server`, `web_server`, `langchain-mcp-adapters`) was gutted in Phase 21: there is no `mcp/` package or MCP container today; Tavily is called directly via `providers/tavily.py`.
>
> **Keep this doc honest.** Phase *descriptions* are an append-only log — don't rewrite history. But the **current-state** sections — Stack, High-level architecture, Repo structure, Reuse, and File map — must track reality. When a phase changes the stack, fix them in the *same commit* as the status flip (workflow step 8). If you spot rot while passing through, fix it then.

## Context

A webapp that helps a candidate prepare for a specific job (started greenfield). Flow: candidate uploads CV/project docs, supplies a job description (raw or URL), the system researches the company, then runs a per-round interview where it asks personalized questions, scores answers, gives feedback, and shows a model answer. v1 targets two round types — **Resume / Project Deep-Dive** and **Behavioral / STAR**.

The stack: FastAPI + React/TypeScript (Vite, replaced the original Streamlit
UI in Phase 18) + Postgres (pgvector) + a separate FastAPI embedder sidecar
(Jina embeddings v3) + LangGraph (multi-agent supervisor) + LangChain + MCP
(Tavily for web, custom server for app-side tools) + Qwen3-8B served by a
llama.cpp CUDA container (`llama`, OpenAI-compatible `/v1`; via `langchain-openai`'s
`ChatOpenAI`) + Docker Compose. Multi-user, JWT + bcrypt auth. SSE streaming with
AbortController on the client. A2A wrapping deferred. STT, Markdown ingestion,
technical/system-design rounds — all out of v1.

The plan is organized as bricks: each phase is independently testable end-to-end before the next is started.

---

## High-level architecture

```
┌─ React + TS UI (container, Vite) ───────┐         llama.cpp server (container)
│  routes: /login / /setup / /interview / │         llama:8080  (OpenAI /v1)
│  /history; typed api.ts client → SSE    │                 ▲
└──────────────┬──────────────────────────┘                 │ ChatOpenAI
               │  HTTPS + JWT                                │
┌──────────────▼──────────────────────────┐                 │
│  FastAPI (container)                     ├─────────────────┘
│   /auth, /documents, /jobs, /sessions    │
│   SSE streaming for question/feedback     │       embedder sidecar (container)
│                                           ├─────▶ embedder:8001  (Jina v3 /embed)
│   LangGraph: prep_graph + interview loop  │
│     profile_builder, job_analyzer,        │       Tavily web API (external)
│     company_researcher,                   ├─────▶ via providers/tavily
│     question_generator, evaluator         │
│   checkpointer: SQLite (file volume)      │
└──────────────┬────────────────────────────┘
               │ SQLAlchemy async
       ┌───────▼───────────────┐
       │  Postgres (pgvector)   │
       │  app data +            │
       │  grounding_chunks      │
       └────────────────────────┘
```

Two persistence layers, intentionally separated:
- **Postgres (pgvector)** — app data: users, documents (raw + parsed), jobs, company snapshots, sessions, turns, evaluations, plus `grounding_chunks` (RAG vectors).
- **SQLite (file volume)** — LangGraph checkpoints (matches `langgraph-checkpoint-sqlite==3.0.3` already in deps). Keeps graph state recovery decoupled from app schema.

---

## Repo structure

```
interview_coach/
  pyproject.toml              # uv-managed
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
    providers/
      tavily.py               # tavily_search / fetch_url_text — web seam (called directly; MCP removed Phase 21)
      base.py                 # SearchResult + provider types
    rag/
      ingest.py / retrieval.py / hybrid.py   # chunk→embed→store; vector + BM25/RRF retrieval
      client.py / tokenizer.py / model_identity.py   # embedder HTTP client, chunking, model constants
    db/
      models.py               # SQLAlchemy 2.0 declarative
      session.py              # async engine + session
      repos.py                # query helpers used by routes
    ingestion/
      pdf.py                  # pypdf
      docx.py                 # python-docx
      normalize.py            # text → structured profile via LLM
    llm/
      client.py               # ChatOpenAI factory (llama.cpp /v1), streaming wrapper, retry
      telemetry.py            # per-call LLM telemetry → llm_calls table
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
| 27    | Arch deepening — prep-event protocol (one typed owner; verdict reason on run+skip, outcome enum) | ✅ |
| 28    | Arch deepening — render prep run reason (setup UI consumes Phase 27 protocol; force_refresh UI cleanup) | ✅ |
| 29    | Arch deepening — activeJob deep setter (D) + collapse providers seam (F) + embedding-identity owner (G) | ✅          |
| 30    | Arch deepening — prep-node glue (A/E) + readiness owner (B) + vector-SQL dedup (C) + ingestion.web shim deletion (D); ADR 0002 routing stays edge-defined | ✅ |
| 31    | CI pipeline — GitHub Actions gating test/lint/fmt on PRs | ⏳ |
| 32    | GitHub ingestion — gather repos from CV/user URL, scrape public repos into grounding | ⏳ |
| 33    | Technical interview round type (grounded in GitHub repos from P32) | ⏳ |
| 34    | Speech-to-text — voice answers in the interview loop | ⏳ |
| 35    | Deployability + CD — one-command setup, docs, LLM-provider switch (local GPU or cloud endpoint by config) | ⏳ |


## Key files

- `docker-compose.yml`, `Dockerfile.api`, `Dockerfile.ui`, `.env.example`, `alembic/`
- `src/interview_coach/api/main.py` — FastAPI app; lifespan boots the LangGraph + SQLite checkpointer
- `src/interview_coach/agents/graph.py` — supervisor StateGraph; central piece
- `src/interview_coach/agents/state.py` — single source of truth for graph state
- `src/interview_coach/agents/nodes/*.py` — one file per agent
- `src/interview_coach/db/models.py` — SQLAlchemy schema (incremental per phase)
- `src/interview_coach/llm/client.py` — `ChatOpenAI` factory against the llama.cpp `/v1` (used by every node)
- `src/interview_coach/providers/tavily.py` — web search/fetch seam (Tavily)
- `frontend/src/pages/InterviewPage.tsx` — streaming chat loop (SSE with AbortController)
- `frontend/src/styles.css` — design tokens + sidebar shell + button system

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
6. **Hand off to the user** — they will run the same smoke test on their side. Push the branch and open a PR into `main` (`gh pr create`). Do not merge.
7. **On user approval**, merge the PR once CI is green (`gh pr merge --squash`), which updates `main` on the remote; then `git checkout main && git pull`. `main` is branch-protected — direct pushes are rejected, so all changes flow through PRs whose `backend` + `frontend` checks must pass (see [ADR 0003](../docs/adr/0003-merge-through-prs-not-fast-forward.md)).
8. **Update `plan/master.md`** — add phase with status ✅, and make changes if anything in this doc needs updating.
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
src/interview_coach/      ← FastAPI app: api, agents, db, ingestion, llm, rag, providers, observability
frontend/                 ← React + TS UI (Vite); built by Dockerfile.ui
alembic/                  ← migrations
tests/                    ← pytest (unit + integration)
docker-compose.yml        ← db (pgvector) + embedder + llama (llama.cpp) + api + ui + adminer
Dockerfile.api            ← runs alembic upgrade head before uvicorn
Dockerfile.ui             ← builds frontend/
```
