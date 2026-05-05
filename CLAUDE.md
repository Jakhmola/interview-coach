# Project guide for coding agents

You are helping build **Interview Coach**, a personalized AI interview practice webapp. The build is phased and gated — read the master plan and follow the workflow. **Do not skip phases or implement ahead.**

## Read first

- [`plan/master.md`](plan/master.md) — full 13-phase plan, status table, architecture, workflow, conventions. **Read this before doing anything.**
- [`plan/current-phase.md`](plan/current-phase.md) — detailed plan for the active phase. Gitignored — present locally, overwritten when a phase finishes. If it's missing, the next step is to draft it (see workflow in master).

## Workflow in one paragraph

For each phase: branch (`phase-N-<slug>`) → write `plan/current-phase.md` → wait for user approval → implement → self-test (smoke test in the plan) → hand off → user tests → on approval fast-forward `main` and push → flip the status row in `plan/master.md` → start the next phase.

Don't start coding without an approved `plan/current-phase.md`. Don't merge without user approval.

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

FastAPI + Streamlit + Postgres + LangGraph (later phases) + MCP (later) + Ollama on host (`qwen3:8b`). JWT + bcrypt auth. Alembic migrations run on `api` container start.

The full architecture and per-phase deliverables live in `plan/master.md` — go read it.
