# Reference Pack delivery checklist

This maps the requested Meridian Reference Pack deliverables to repository files.
Large raw/layer databases and vector indexes are intentionally rebuildable local
artifacts rather than source-controlled files.

| Requested item | Delivered location | Notes |
|---|---|---|
| Frozen source and Bronze audit trail | `src/meridian_assistant/bronze.py`, `bronze/README.md` | Copies the selected raw DuckDB source unchanged and writes population/provenance/checksum manifest. |
| Silver transformations | `src/meridian_assistant/silver.py`, `silver/README.md` | Canonical names/types, date parsing, null handling, brand mapping, and one record per Complaint ID. |
| Gold analytical tables | `src/meridian_assistant/gold.py`, `gold/README.md` | Materializes query-ready monthly metrics and fails a build on reconciliation mismatch. |
| Atomic/rebuildable publication | `src/meridian_assistant/publication.py` | Generation directories plus a `current` pointer prevent a partial generation being consumed. |
| Gold NL query harness | `src/meridian_assistant/analytics.py` | Typed, allowlisted plans compile to parameterized read-only DuckDB SQL; executed SQL is retained. |
| Narrative RAG | `scripts/build_rag_index.py`, `src/meridian_assistant/retrieval.py` | Canonical Silver source, hard metadata filters, generation manifest, Complaint ID evidence. |
| Orchestration and grounding | `assistant.py`, `semantic_planner.py`, `synthesis.py`, `validation.py` | LLM interpretation is bounded by typed tools; validation remains deterministic. |
| Runnable interface | `api.py`, `cli.py`, `README.md` | Flask `/api/v1/answers`, health/snapshot/questions endpoints, CLI and batch runner. |
| Fifteen-answer delivery file | `answers.json` | One generated record per supplied question; includes SQL where applicable. |
| Trace/evidence artifacts | `artifacts/run/.final-15-v4.a4o94doa/` | Local generated answers, tool traces, and evidence bundle from the pinned run. |
| Design/write-up | `DECISIONS.md` | Source choice, architecture, trade-offs, 10×/daily-refresh plan. |
| Evaluation | `EVALUATION.md`, `tests/` | Pipeline, NL-query, retrieval/filter, grounding, and abstention checks. |

## Rebuild order

```bash
# 1. Bronze: raw, pinned source → immutable source generation
PYTHONPATH=src uv run python -m meridian_assistant.bronze \
  --source data/complaints.duckdb \
  --bronze-db bronze/complaints.duckdb \
  --manifest bronze/manifest.json

# 2. Silver: Bronze → canonical complaints
PYTHONPATH=src uv run python -m meridian_assistant.silver \
  --source bronze/current/complaints.duckdb \
  --silver-db silver/complaints.duckdb \
  --quality-report silver/quality-report.json

# 3. Gold: Silver → query-ready aggregates
PYTHONPATH=src uv run python -m meridian_assistant.gold \
  --silver-db silver/current/complaints.duckdb \
  --gold-db gold/metrics.duckdb \
  --manifest gold/manifest.json

# 4. Optional, explicit external-processing step: build the RAG generation
PYTHONPATH=src uv run python scripts/build_rag_index.py
```

A generation exposes the following stable consuming paths after a successful build:

```text
bronze/current/complaints.duckdb   bronze/current/manifest.json
silver/current/complaints.duckdb   silver/current/quality-report.json
gold/current/metrics.duckdb        gold/current/manifest.json
```

## What is intentionally not shipped

- `data/complaints.duckdb`: large local source snapshot;
- generated Bronze/Silver/Gold DuckDB files: rebuildable and large;
- Chroma/index embedding artifacts: rebuildable and provider-dependent;
- API keys and `.env` files.

The code, layer-level documentation, command sequence, tests, generated answer
contract, and written decisions/evaluation are shipped so a reviewer can recreate
and inspect every transformation.
