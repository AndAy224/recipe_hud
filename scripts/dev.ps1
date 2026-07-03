# Windows dev: run the backend with hot reload and the mock display backend.
# Usage: .\scripts\dev.ps1   (from the repo root, after creating .venv)

$env:RECIPEHUD_DISPLAY_BACKEND = "mock"
$env:RECIPEHUD_DEBUG = "1"

$repo = Split-Path -Parent $PSScriptRoot
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\seed_db.py"
& "$repo\.venv\Scripts\uvicorn.exe" recipehud.main:app --reload --host 0.0.0.0 --port 8000 --app-dir "$repo\backend"
