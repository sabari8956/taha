# Meridian Complaint Intelligence

An auditable LangGraph prototype for answering structured and narrative questions over the CFPB Consumer Complaint Database.

See [PRD.md](PRD.md) for product scope, [docs/DELIVERY.md](docs/DELIVERY.md) for a Reference-Pack-to-file checklist, [DECISIONS.md](DECISIONS.md) for the delivery write-up, [EVALUATION.md](EVALUATION.md) for validation evidence, and [AGENTS.md](AGENTS.md) for the engineering workflow.

## Setup

```bash
uv sync
uv run python main.py --diagram  # mock/architecture diagram only
```

The command creates `artifacts/meridian_langgraph_flow.png`, a LangGraph-rendered explanation of the proposed assistant flow.

To run the current mocked agent loop:

The production path uses the deterministic planner, guarded evidence tools, template
synthesis, and claim validator (no live chat model):

```bash
uv run python -m meridian_assistant answer --id q03 \
  --question "Of the ten companies with the most complaints overall, which one is worst at responding on time — and what do consumers say happened in those late cases?"
uv run python -m meridian_assistant batch --questions data/questions.json \
  --artifacts-root artifacts/run
```

`answer` prints the stable five-field answer record. `batch` atomically publishes
`answers.json`, `tool-traces.json`, and bounded `evidence.json`, then prints the immutable
run destination. Grounded abstentions are successful outputs.

## Flask API

The HTTP adapter exposes the same deterministic planner and guarded tools as the CLI. It does
not accept SQL, filters, or tool instructions from callers. Start it with the paths appropriate
to your published data generations:

```bash
PYTHONPATH=src uv run python -m meridian_assistant serve \
  --gold-db gold/current/metrics.duckdb \
  --bronze-manifest bronze/current/manifest.json \
  --rag-index artifacts/rag/chroma-v2 \
  --host 127.0.0.1 --port 5000
```

The Flask service is **LLM-first by default**: it uses GPT-5.6 Luna for semantic
planning and evidence-bounded narration. Set the server key before starting it:

```bash
export OPENAI_API_KEY=...
PYTHONPATH=src uv run python -m meridian_assistant serve \
  --gold-db gold/current/metrics.duckdb \
  --bronze-manifest bronze/current/manifest.json \
  --rag-index artifacts/rag/chroma-v2
```

Both LLM paths use **GPT-5.6 Luna** through the OpenAI API and require
`OPENAI_API_KEY`. The semantic planner receives the question and tool-schema contract, then
emits a strict JSON plan that is compiled to allowlisted typed queries—never model-authored SQL.
The narrator receives only the bounded evidence bundle and emits claim references, not final
unchecked prose. Deterministic validation still verifies every numeric row, Complaint ID, quote,
filter, and citation before rendering. Enabling narration sends retrieved complaint excerpts to
the configured OpenAI endpoint; obtain the appropriate data-handling approval before enabling it.
The legacy deterministic keyword planner is available only through explicit dependency injection
for offline/testing integrations. The normal Flask server never silently falls back to it: an
unavailable or malformed LLM plan/draft fails closed with an abstention.


Endpoints:

```bash
curl http://127.0.0.1:5000/healthz

curl -X POST http://127.0.0.1:5000/api/v1/answers \
  -H 'Content-Type: application/json' \
  -d '{"question_id":"q03","question":"Of the ten companies with the most complaints overall, which one is worst at responding on time?"}'
```

`POST /api/v1/answers` requires nonblank string `question_id` and `question` fields. Successful
responses include the answer record, a serialized audit trace, and bounded evidence metadata
(excerpts are intentionally not returned). Invalid input returns a JSON `400`; unexpected server
errors return a generic JSON `500` and are logged without returning internal details. Narrative
requests may require the configured embedding-provider environment variables; do not put those
credentials in request payloads or source control.

> `main.py` is a diagram/mock-only illustration. Production execution is `python -m meridian_assistant` and uses the pipeline-backed DuckDB/RAG adapters.

## Local input data

`data/complaints.duckdb` is the existing local DuckDB source input. It is intentionally ignored by Git due to its size and is always opened read-only.

## Reproducible DuckDB medallion build

Large generated layer databases, RAG indexes, and the raw source are deliberately excluded from Git. The committed `bronze/README.md`, `silver/README.md`, and `gold/README.md` document each stage; build the generated artifacts explicitly:

```bash
PYTHONPATH=src uv run python -m meridian_assistant.bronze \
  --source data/complaints.duckdb --bronze-db bronze/complaints.duckdb \
  --manifest bronze/manifest.json
PYTHONPATH=src uv run python -m meridian_assistant.silver \
  --source bronze/current/complaints.duckdb --silver-db silver/complaints.duckdb \
  --quality-report silver/quality-report.json
PYTHONPATH=src uv run python -m meridian_assistant.gold \
  --silver-db silver/current/complaints.duckdb --gold-db gold/metrics.duckdb \
  --manifest gold/manifest.json
```

Each command publishes a matched DB + JSON **generation**. Given the requested outputs
`bronze/complaints.duckdb` and `bronze/manifest.json`, the visible pair is
`bronze/current/complaints.duckdb` and `bronze/current/manifest.json`; immutable build
artifacts live in `bronze/.generations/<generation-id>/`. The `current` directory is atomically
switched only after both artifacts are complete, so a failed build preserves the old matched
pair. Use the same layout for silver and gold, and pass each visible `current` database to the
next command.

Bronze is a byte-for-byte validated source snapshot plus provenance manifest. Supply CFPB
source URL/endpoint, acquisition method, source version, and retrieval timestamp with
`--source-url`, `--acquisition-method`, `--source-version`, and `--snapshot-at` (unavailable
values may be omitted). It validates every record against the declared inclusive 2024-01-01 to
2025-12-31 date range and nonblank-narrative criterion, reports observed/violation counts, and
fails by default. `--allow-population-violations` is solely for intentionally retaining an
exception snapshot for audit.

Silver retains one record per trimmed Complaint ID (lowest source DuckDB `rowid` wins),
normalizes dates/text, and emits quality counts. Gold reads only silver and aggregates dated
complaints. `timely_response_rate` is `timely_response_count / timely_response_known_count`;
only true/false timely values are known, and the rate is NULL when none are known. Its manifest
reconciles every metric table total to silver's dated-record count.

## Narrative retrieval (RAG)

Build the RAG index only after Silver is available and external processing has been
explicitly approved: narrative text is sent to the configured embedding provider. It reads the
published canonical `silver/current/complaints.duckdb`, rather than raw source data, and
publishes a clean, fully reconciled index plus its manifest as an immutable generation behind
`artifacts/rag/chroma/current/`. Incremental/resume builds are intentionally unsupported.

The metadata schema is versioned. Hard date filters use integer
`date_received_ordinal` values while retaining ISO `date_received` for deterministic audit
validation. Generations without metadata schema version 2 are refused before any embedding
request and must be rebuilt cleanly; there is no in-place migration. Rebuilding resends indexed
narrative text to the configured provider, so external-processing approval and credentials are
required. Only a fully reconciled generation is atomically published as `current`; do not commit
generated index artifacts.

```bash
export OPENAI_API_KEY=...
PYTHONPATH=src uv run python scripts/build_rag_index.py
PYTHONPATH=src uv run python scripts/query_rag.py "Cash App account problems" \
  --company "Cash App" --start 2025-01-01 --end 2026-01-01
```

The index stores canonical Complaint IDs as strings without coercion. Hard filters support
company/brand, product, sub-product, issue, sub-issue, state, timely response, company response,
and an inclusive-start/exclusive-end date range. Returned narrative text is an excerpt: narratives
longer than 6,000 characters are deliberately truncated, and the manifest records their count.

**Data handling:** building/querying with the default embedding function sends narrative text to
OpenAI to create query and document embeddings. This is an external-processing boundary; use it
only when that handling is approved for the dataset.

## Delivery inventory

| Reference-pack deliverable | Location |
|---|---|
| Rebuildable Bronze → Silver → Gold pipeline | `src/meridian_assistant/{bronze,silver,gold,publication}.py` |
| Layer contracts and build commands | `bronze/README.md`, `silver/README.md`, `gold/README.md` |
| Guarded NL → Gold SQL harness | `src/meridian_assistant/analytics.py` |
| Metadata-filtered RAG builder/retriever | `scripts/build_rag_index.py`, `src/meridian_assistant/retrieval.py` |
| LLM planning, grounded narration, API | `semantic_planner.py`, `synthesis.py`, `api.py` |
| Fifteen generated answer records | `answers.json` |
| Evidence and tool traces for the latest packaged local run | `artifacts/run/.final-15-v4.a4o94doa/` |
| Decisions/write-up | `DECISIONS.md` |
| Evaluation/checks and limits | `EVALUATION.md` |

### Quick path

If a reviewer has an already-built local data generation and RAG generation, point the
service at those `current` paths and run the Flask server. If not, use the layer build
commands above in order, then build the documented RAG working set. `answers.json` is a
checked-in generated delivery artifact from the pinned snapshot; regenerate it with a
distinct batch run ID after rebuilding input artifacts.
