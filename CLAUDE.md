# Project guide for coding agents

You are helping build **Interview Coach**, a personalized AI interview practice webapp. The build is phased and gated — read the master plan and follow the workflow. **Do not skip phases or implement ahead.**

## Read first

- [`plan/master.md`](plan/master.md) — full phased plan, status table, architecture, workflow, conventions. **Read this before doing anything.**
- [`plan/current-phase.md`](plan/current-phase.md) — detailed plan for the active phase. Gitignored — present locally, overwritten when a phase finishes. If it's missing, the next step is to draft it (see workflow in master).

## Workflow in one paragraph

For each phase: branch (`phase-N-<slug>`) → write `plan/current-phase.md` → wait for user approval → implement → self-test (smoke test in the plan) → hand off → user tests → on approval fast-forward `main` and push → flip the status row in `plan/master.md` → start the next phase.

Don't start coding without an approved `plan/current-phase.md`. Don't merge without user approval.

## How to write code

Apply `/karpathy-guidelines` whenever you write, review, or refactor: smallest surgical change, no overcomplication, surface assumptions, define a verifiable "done." Past arch-deepening phases loaded it before implementing — keep doing that.

## Commands you'll use

```sh
make up          # docker compose up -d --build
make down        # tear down
make test        # uv run pytest -q  (host, in-memory SQLite for DB tests)
make fmt         # ruff format
make lint        # ruff check
make ps          # service status
make logs        # tail logs
```

## Stack snapshot

FastAPI + React/TypeScript (Vite) + Postgres (pgvector) + a Jina-embeddings sidecar (`embedder`) + a LangGraph supervisor + Tavily web search/fetch (via a `providers/` seam — MCP was removed in Phase 21) + Qwen3-8B served by a llama.cpp CUDA container (`llama`, OpenAI-compatible `/v1`). JWT + bcrypt auth. Alembic migrations run on `api` container start.

The full architecture and per-phase deliverables live in `plan/master.md` — go read it.

## Agent skills

### Issue tracker

Issues and PRDs live as markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles using their default names (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
