FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml README.md ./
RUN uv venv /app/.venv && uv pip install --no-cache .

COPY embedder ./embedder


FROM python:3.13-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/cache/hf

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/embedder /app/embedder

EXPOSE 8001
CMD ["uvicorn", "embedder.main:app", "--host", "0.0.0.0", "--port", "8001"]
