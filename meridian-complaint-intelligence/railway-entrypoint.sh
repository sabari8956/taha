#!/bin/sh
set -eu

DATA_ROOT="${DATA_ROOT:-/data}"
GOLD_DB="${GOLD_DB:-$DATA_ROOT/gold/current/metrics.duckdb}"
BRONZE_MANIFEST="${BRONZE_MANIFEST:-$DATA_ROOT/bronze/current/manifest.json}"
RAG_INDEX="${RAG_INDEX:-$DATA_ROOT/artifacts/rag/chroma}"
QUESTIONS="${QUESTIONS_PATH:-/app/data/questions.json}"

exec /app/.venv/bin/gunicorn \
  --bind "0.0.0.0:${PORT:-5057}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --access-logfile - \
  railway_app:app
