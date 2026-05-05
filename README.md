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

## LLM setup — `llama-server` on host

The agents (Phase 6+) call an OpenAI-compatible local server. We use
`llama.cpp`'s `llama-server` running on the **host** in its own GPU
container; the api container reaches it via `host.docker.internal:8080`.

### One-time: download the GGUF

```sh
mkdir -p ~/models
# Pick one (the second is much smaller for slow boxes):
huggingface-cli download unsloth/Qwen3-8B-GGUF Qwen3-8B-Q4_K_M.gguf --local-dir ~/models
```

(If you don't have `huggingface-cli`: `pipx install -U "huggingface_hub[cli]"`.)

### Run llama-server (GPU, OpenAI-compatible)

```sh
docker rm -f llama-server 2>/dev/null
docker run -d --gpus all \
  -v ~/models:/models \
  -p 8080:8080 \
  --name llama-server \
  ghcr.io/ggml-org/llama.cpp:server-cuda \
  -m /models/Qwen3-8B-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --jinja \
  --alias qwen3-8b
```

Verify:

```sh
curl http://localhost:8080/v1/models | jq
curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-8b","messages":[{"role":"user","content":"hi in 3 words"}]}'
```

Settings come from `.env`:
- `LLM_BASE_URL=http://localhost:8080/v1` (host runs); compose overrides to
  `http://host.docker.internal:8080/v1` for the api container.
- `MODEL_NAME=qwen3-8b` (matches `--alias` on the server).

### Quick LLM check from Python

```sh
INTEGRATION=1 uv run pytest tests/test_llm.py::test_real_llm_streaming -v
```

## Layout

```
src/interview_coach/    # FastAPI app + (later) agents, MCP, db, llm, ingestion
ui/                     # Streamlit app
tests/                  # pytest
```
