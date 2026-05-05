# interview-coach

Personalized AI interview practice. Upload a CV + project docs, paste a job description, pick a round type (Resume Walkthrough or Behavioral / STAR), and the system asks tailored questions, scores answers, and gives feedback + a model answer.

## Stack

- FastAPI + Streamlit
- LangGraph multi-agent supervisor (LangChain + MCP tools)
- Ollama on host (default `qwen3:8b`)
- Postgres (app data) + SQLite (LangGraph checkpoints)
- Docker Compose for the whole stack

See `interview-coach-master-plan.md` in the project's plan directory for the full 13-phase build.

## Quick start

```sh
cp .env.example .env
# (Optional) put your TAVILY_API_KEY in .env if you'll fetch JDs from URLs

make up
curl http://localhost:8000/healthz   # → {"status":"ok",...}
open http://localhost:8501           # Streamlit
```

Run tests on the host (no container needed):

```sh
uv sync
make test
```

## Ollama setup (host, not container)

The agents (Phase 6+) call Ollama running on your **host** machine; the api
container reaches it via `host.docker.internal:11434`.

```sh
# Install Ollama: https://ollama.com/download
ollama pull qwen3:8b      # ~5 GB; one-time
ollama serve              # if not already running as a service
```

Override the default with `MODEL_NAME` in `.env` (e.g., a smaller model for
testing on a slow box). `OLLAMA_BASE_URL` defaults to
`http://host.docker.internal:11434`.

Quick LLM check (host):

```sh
INTEGRATION=1 uv run pytest tests/test_llm_ollama.py::test_real_ollama_streaming -v
```

## Layout

```
src/interview_coach/    # FastAPI app + (later) agents, MCP, db, llm, ingestion
ui/                     # Streamlit app
tests/                  # pytest
```
