# Gold — guarded analytical metrics

Gold reads **only** Silver and materializes the DuckDB aggregate tables used by
the structured-analysis harness.

Generated artifacts (not committed):

```text
gold/current/
├── metrics.duckdb
└── manifest.json
```

## Materialized tables

- `company_month_metrics`
- `company_product_month_metrics`
- `product_month_metrics`
- `company_issue_month_metrics`
- `product_issue_sub_issue_month_metrics`
- `company_product_state_issue_month_metrics`
- `company_response_month_metrics`

Every table includes complaint volume plus timely-response numerator, known
response denominator, and rate. The manifest reconciles each aggregate table's
complaint total to dated Silver records; the build fails if a reconciliation fails.

The assistant does not execute model-authored SQL. It validates a typed plan and
compiles parameterized, read-only SQL against these allowlisted tables.

## Build

```bash
PYTHONPATH=src uv run python -m meridian_assistant.gold \
  --silver-db silver/current/complaints.duckdb \
  --gold-db gold/metrics.duckdb \
  --manifest gold/manifest.json
```
