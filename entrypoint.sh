#!/usr/bin/env bash
set -euo pipefail

alembic upgrade head
exec uvicorn interview_coach.api.main:app --host 0.0.0.0 --port 8000
