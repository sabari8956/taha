# Bronze — frozen source snapshot

This folder is the immutable landing layer for the pinned CFPB extract: complaints
received from **2024-01-01 through 2025-12-31** with consumer narratives.

Generated files are deliberately excluded from Git because the source snapshot is
multi-gigabyte. After a build, the visible, consumed artifacts are:

```text
bronze/current/
├── complaints.duckdb  # byte-for-byte copy of the selected source snapshot
└── manifest.json      # provenance, population checks, counts, checksum
```

`current` is a symlink to an immutable directory under `.generations/`; publication
switches the pointer only after the database and manifest are complete.

## Build

```bash
PYTHONPATH=src uv run python -m meridian_assistant.bronze \
  --source data/complaints.duckdb \
  --bronze-db bronze/complaints.duckdb \
  --manifest bronze/manifest.json \
  --source-url 'https://www.consumerfinance.gov/data-research/consumer-complaints/' \
  --acquisition-method local_duckdb_snapshot \
  --source-version pinned-2024-2025-narratives \
  --snapshot-at 2026-07-20T00:00:00Z
```

Bronze does not transform rows. It copies first, validates the declared population,
and records source location, acquisition method, timestamps, record counts, observed
date range, narrative count, Complaint-ID count, and SHA-256 checksum.
