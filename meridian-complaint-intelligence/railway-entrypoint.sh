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
  'meridian_assistant.api:create_app(gold_db=__import__("pathlib").Path("'"$GOLD_DB"'"), bronze_manifest=__import__("pathlib").Path("'"$BRONZE_MANIFEST"'"), rag_index=__import__("pathlib").Path("'"$RAG_INDEX"'"), questions_path=__import__("pathlib").Path("'"$QUESTIONS"'"), enable_cors=True, semantic_planner_model=__import__("os").environ.get("SEMANTIC_PLANNER_MODEL", "gpt-5.6-luna"), narrator_model=__import__("os").environ.get("NARRATOR_MODEL", "gpt-5.6-luna"))'
