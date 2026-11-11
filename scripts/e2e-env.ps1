# Windows PowerShell: environment for local E2E/demo runs (APP_ENV=test + scripted LLM TEST DOUBLE).
# Usage:  . .\scripts\e2e-env.ps1    (note the leading dot so variables stay in your session)
$env:APP_ENV = "test"
$env:LLM_PROVIDER = "scripted"
if (-not $env:E2E_DATABASE_URL) { $env:E2E_DATABASE_URL = "postgresql://nbkyc@127.0.0.1:54329/nbkyc_dev" }
$env:DATABASE_URL = $env:E2E_DATABASE_URL
$env:SESSION_SECRET = "e2e-session-secret-not-for-production-000000"
$env:ENCRYPTION_KEY = "ZTJlLWtleS1ub3QtZm9yLXByb2R1Y3Rpb24tMzJieXQ"
$env:IDENTITY_HMAC_SECRET = "e2e-hmac-secret-not-for-production-0000000"
$env:STORAGE_DIR = Join-Path $PSScriptRoot "..\backend\var\e2e-storage"
$env:LOG_JSON = "false"
Write-Host "E2E test environment loaded (APP_ENV=test, scripted LLM test double). Never use it for real documents."
