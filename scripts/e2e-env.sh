#!/usr/bin/env bash
# Environment for local E2E / demo runs: APP_ENV=test + the scripted LLM TEST DOUBLE.
# Never use this for real documents. Override E2E_DATABASE_URL to point at your own Postgres.
export APP_ENV=test LLM_PROVIDER=scripted
export DATABASE_URL="${E2E_DATABASE_URL:-postgresql://nbkyc@127.0.0.1:54329/nbkyc_dev}"
export SESSION_SECRET="e2e-session-secret-not-for-production-000000"
export ENCRYPTION_KEY="ZTJlLWtleS1ub3QtZm9yLXByb2R1Y3Rpb24tMzJieXQ"
export IDENTITY_HMAC_SECRET="e2e-hmac-secret-not-for-production-0000000"
export STORAGE_DIR="${E2E_STORAGE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/backend/var/e2e-storage}"
export LOG_JSON=false WORKER_POLL_SECONDS=0.5 WORKER_HEARTBEAT_SECONDS=5
