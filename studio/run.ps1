# SpotAlert Studio — native local launch (no Docker, no NAS deploy).
# Runs directly on this PC so it can write to a local Lightroom .lrcat catalog
# (Lightroom must be closed while Studio is organizing photos — see README.md).

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Output "Creating virtual environment..."
    python -m venv .venv
}

& ".venv\Scripts\pip.exe" install -q -r requirements.txt

Write-Output "Starting SpotAlert Studio on http://127.0.0.1:5000 ..."
& ".venv\Scripts\waitress-serve.exe" --host=127.0.0.1 --port=5000 app:app
