#!/bin/sh
# Host-side development against the compose Postgres: migrate, import once, serve with reload.
set -e
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "copy .env.example to .env first"; exit 1; }
set -a; . ./.env; set +a
docker compose up -d --wait db
uv sync
uv run materials-db migrate
uv run materials-db import-rii --if-needed --crossref
echo "moderator token for trying submissions:"
uv run materials-db token --sub dev-moderator --name "Dev Moderator" --role moderator
exec uv run materials-db serve --reload
