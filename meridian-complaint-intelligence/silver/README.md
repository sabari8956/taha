# Silver — canonical complaint records

Silver reads **only** the published Bronze database and creates a canonical
one-row-per-Complaint-ID table.

Generated artifacts (not committed):

```text
silver/current/
├── complaints.duckdb
└── quality-report.json
```

## Transformations

- standardizes source columns into the canonical complaint schema;
- parses received/sent dates and normalizes text fields;
- converts timely-response values to a typed boolean/null field;
- normalizes company identity and records curated consumer brands;
- keeps the lowest source DuckDB `rowid` after trimming Complaint ID;
- reports duplicate rows skipped, null counts, invalid-row count, and brand counts.

Near-duplicate narrative wording is retained: each unique Complaint ID remains one
real analytical complaint. Retrieval may later diversify results without changing
analytical counts.

## Build

```bash
PYTHONPATH=src uv run python -m meridian_assistant.silver \
  --source bronze/current/complaints.duckdb \
  --silver-db silver/complaints.duckdb \
  --quality-report silver/quality-report.json
```

Inspect the resulting `quality-report.json` before promoting a generation.
