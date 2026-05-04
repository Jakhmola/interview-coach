# interview-coach

Personalized AI interview practice. Upload a CV + project docs, paste a job description, pick a round type (Resume Walkthrough or Behavioral / STAR), and the system asks tailored questions, scores answers, and gives feedback + a model answer.

## Stack

- FastAPI + Streamlit
- LangGraph multi-agent supervisor (LangChain + MCP tools)
- Ollama on host (default `qwen3:8b`)
- Postgres (app data) + SQLite (LangGraph checkpoints)
- Docker Compose for the whole stack

See `interview-coach-master-plan.md` in the project's plan directory for the full 13-phase build.

## Quick start (Phase 0 — skeleton only)

```sh
cp .env.example .env
make up
curl http://localhost:8000/healthz   # → {"status":"ok",...}
open http://localhost:8501           # Streamlit hello page
```

Run tests on the host (no container needed):

```sh
uv sync
make test
```

## Layout

```
src/interview_coach/    # FastAPI app + (later) agents, MCP, db, llm, ingestion
ui/                     # Streamlit app
tests/                  # pytest
```
