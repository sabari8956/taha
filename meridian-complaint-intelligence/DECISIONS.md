# Meridian decisions and production follow-up

## Source and snapshot

The system uses the CFPB Consumer Complaint Database because it supplies both
standardized complaint fields and consumer narratives. The implementation accepts a
local DuckDB snapshot rather than querying a live endpoint at answer time. The
pinned population is complaints received 2024-01-01 through 2025-12-31 with a
nonblank consumer narrative. Bronze records the source URL/method/version supplied
at build time, observed coverage, counts, population violations, and a file checksum.
This keeps every reported number reproducible after CFPB's daily public updates.

## Data layers

Bronze is an untouched byte copy plus manifest. Silver conforms field names/types,
parses dates, normalizes text, deduplicates by trimmed Complaint ID (lowest source
DuckDB rowid wins), and applies the documented legal-entity/consumer-brand registry.
Gold materializes monthly aggregate tables for company, product, issue, state, and
company-response analytical shapes. Each Gold table reconciles to Silver's dated
complaint count before publication.

Each builder writes a fresh immutable `.generations/<id>` directory and atomically
advances `current` only after its matched DuckDB and report JSON are complete. Large
source/layer databases and vector stores are intentionally not committed; their
builders, schemas, reports, and instructions are committed.

## Assistant architecture

The HTTP service is LLM-first for semantic interpretation and evidence-bounded
narration. The LLM never receives database write access and never authors SQL. A
semantic plan is parsed into typed, allowlisted filters/operations; the structured
tool compiles parameterized read-only DuckDB SQL only against Gold. Narrative
retrieval filters canonical Silver-derived metadata before vector search and retains
Complaint IDs. A validator verifies structured references, filters, citations, and
quoted excerpts before rendering. If planning, retrieval, validation, or source
coverage fails, the system abstains rather than broadening a request or inventing a
claim.

## Retrieval scope and data handling

Embedding all ~2M narratives is intentionally out of scope. The documented
reproducible working set contains 15,000 stratified canonical-Silver narratives,
including slices required by the supplied questions. Gold calculations remain
full-population; retrieved narratives are illustrations, not prevalence estimates.
Embedding and LLM narration are explicit external-processing boundaries. Keys are
server environment variables and are never committed or accepted in HTTP payloads.

## Scaling and daily refresh

At 10x volume, DuckDB aggregate builds would move to partitioned Parquet/object
storage and a warehouse with incremental partition refreshes; the vector store would
be sharded or metadata-prefiltered before embedding. A daily CFPB refresh would:

1. acquire the source once with a timestamp/version;
2. stage it under a new Bronze generation;
3. validate schema, row count, date/narrative population, and checksum;
4. canonicalize idempotently in Silver keyed by Complaint ID;
5. rebuild or incrementally update affected Gold periods;
6. index only new/changed eligible narratives;
7. reconcile, monitor row/count/null/schema-drift thresholds, and publish only on
   successful checks.

Failures leave the prior `current` generation intact. Production would add scheduled
orchestration, alerting, source provenance retention, least-privilege service
accounts, encryption/access controls, request tracing, and model/provider health
monitoring.
