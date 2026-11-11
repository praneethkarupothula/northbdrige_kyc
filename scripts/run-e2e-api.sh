#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/e2e-env.sh"
cd "$(dirname "$0")/../backend"
uv run python -m app.cli migrate
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
