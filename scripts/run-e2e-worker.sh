#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/e2e-env.sh"
cd "$(dirname "$0")/../backend"
exec uv run python -m app.cli worker
