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

## LLM setup — `llama-server` (compose service)

The agents (Phase 6+) call an OpenAI-compatible local server. We run
`llama.cpp`'s `llama-server` as a **compose service** named `llama`,
with GPU passthrough. `make up` starts it alongside everything else.

### One-time: download the GGUF

```sh
mkdir -p ~/models
huggingface-cli download unsloth/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf \
  --local-dir ~/models
```

(If you don't have `huggingface-cli`: `pipx install -U "huggingface_hub[cli]"`.)

By default, compose bind-mounts `~/models` read-only at `/models` and looks
for `Qwen3-8B-Q4_K_M.gguf`. Override with `MODELS_DIR` / `MODEL_FILE` in
`.env` if your file is elsewhere.

### Prerequisites

- Docker with the **NVIDIA Container Toolkit** installed (so `--gpus all`
  works). On CachyOS / Arch: `pacman -S nvidia-container-toolkit` then
  `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.

### Bring it up

```sh
make up   # starts db, llama, api, ui in dependency order
```

Cold start: `llama-server` takes ~30–60s to load the GGUF onto the GPU.
The `api` container starts in parallel; the *first* agent call may be
slow while the model finishes loading. Subsequent calls are fast.

Verify:

```sh
curl http://localhost:8080/v1/models | jq
curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-8b","messages":[{"role":"user","content":"hi in 3 words"}]}'
```

### Routing

- **Inside compose:** the `api` container reaches the LLM at
  `http://llama:8080/v1` (set in `docker-compose.yml`).
- **From host** (pytest, scripts): the port is published, so `.env` uses
  `http://localhost:8080/v1`.

### Quick LLM check from Python

```sh
INTEGRATION=1 uv run pytest tests/test_llm.py::test_real_llm_streaming -v
```

## Useful URLs (with `make up`)

| Service          | URL                          |
| ---------------- | ---------------------------- |
| Streamlit UI     | http://localhost:8501        |
| FastAPI docs     | http://localhost:8000/docs   |
| Adminer (DB UI)  | http://localhost:8090        |
| llama.cpp server | http://localhost:8080        |

`make db-ui` prints the Adminer login fields for the local DB.
`make sh-db` opens psql in the db container.

## Eval harness (Phase 12a)

A question-quality baseline harness lives under `tests/integration/eval/`.
It drives the real `stream_question` against the local LLM for each
fixture × round-type, computes three metrics (distinctness, profile
groundedness, JD relevance), and appends a row per case to a gitignored
`results.csv`. Subsequent phases (variety, RAG) refresh the CSV with
their own `phase` tag for cross-phase comparison.

```sh
# Make sure the local LLM is reachable (e.g., `make up`).
INTEGRATION=1 uv run pytest tests/integration/eval -k quality -v

# Print the comparison table.
uv run python -m tests.integration.eval.report

# Iterate on a single fixture × round (~50–90s):
INTEGRATION=1 uv run pytest tests/integration/eval \
  -k 'backend_senior and resume_walkthrough' -v
```

`make test` (the default) skips the harness — it never touches the LLM.

## Observability (optional)

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (and optionally
`LANGFUSE_HOST`) in `.env` to send per-request LangGraph traces to
Langfuse. Each `prepare`, `next_question`, or `submit_answer` call
becomes one trace, tagged with `user_id`, `session_id`, `round_type`,
and `turn_index`. With the env vars unset the app behaves identically:
no SDK init, no callback, no network calls.

## Layout

```
src/interview_coach/    # FastAPI app + agents, observability, MCP, db, llm
ui/                     # Streamlit app
tests/                  # pytest
```
