# Host-side development against the compose Postgres: migrate, import once, serve with reload.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path .env)) { throw "copy .env.example to .env first" }
foreach ($line in Get-Content .env) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] }
}
docker compose up -d --wait db
uv sync
uv run materials-db migrate
uv run materials-db import-rii --if-needed --crossref
Write-Host "moderator token for trying submissions:"
uv run materials-db token --sub dev-moderator --name "Dev Moderator" --role moderator
uv run materials-db serve --reload
