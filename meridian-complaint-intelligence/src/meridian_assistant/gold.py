"""Atomic DuckDB gold metrics builder; it reads only a silver database."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from meridian_assistant.publication import current_artifact_path, publish_generation


def rebuild_gold(
    silver_db: str | Path, gold_db: str | Path, manifest_path: str | Path
) -> dict[str, Any]:
    """Materialize auditable monthly aggregates from silver with atomic replacement."""
    silver = Path(silver_db).expanduser().resolve(strict=True)
    gold = Path(gold_db).expanduser().resolve()
    manifest_output = Path(manifest_path).expanduser().resolve()
    if len({silver, gold, manifest_output}) != 3:
        raise ValueError("silver, gold database, and manifest must not overlap")
    gold.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{gold.name}.", suffix=".tmp", dir=gold.parent)
    os.close(fd)
    temporary = Path(temp_name)
    temporary.unlink()
    literal = str(silver).replace("'", "''")
    try:
        connection = duckdb.connect(str(temporary))
        try:
            connection.execute(f"ATTACH '{literal}' AS silver_db (READ_ONLY)")
            columns = {
                row[0] for row in connection.execute("DESCRIBE silver_db.complaints").fetchall()
            }
            required = {
                "complaint_id",
                "company",
                "product",
                "issue",
                "company_response_to_consumer",
                "date_received",
                "timely_response",
                "state",
            }
            if missing := sorted(required - columns):
                raise ValueError(f"silver complaints missing columns: {', '.join(missing)}")
            base = """SELECT *, date_trunc('month', date_received)::DATE AS complaint_month
                       FROM silver_db.complaints WHERE date_received IS NOT NULL"""
            metrics = """count(*) AS complaint_count,
                count(*) FILTER (WHERE timely_response) AS timely_response_count,
                count(*) FILTER (WHERE timely_response IS NOT NULL)
                  AS timely_response_known_count,
                timely_response_count::DOUBLE / nullif(timely_response_known_count, 0)
                  AS timely_response_rate"""
            dimensions = {
                "company_product_month_metrics": "company, product, complaint_month",
                "company_month_metrics": "company, complaint_month",
                "product_month_metrics": "product, complaint_month",
                "company_issue_month_metrics": "company, issue, complaint_month",
                "product_issue_sub_issue_month_metrics": (
                    "product, issue, sub_issue, complaint_month"
                ),
                "company_product_state_issue_month_metrics": (
                    "company, product, state, issue, complaint_month"
                ),
                "company_response_month_metrics": (
                    "company, company_response_to_consumer, complaint_month"
                ),
            }
            for name, group_by in dimensions.items():
                query = (
                    f"CREATE TABLE {name} AS SELECT {group_by}, {metrics} "
                    f"FROM ({base}) GROUP BY {group_by}"
                )
                connection.execute(query)
            silver_rows = connection.execute(
                "SELECT count(*) FROM silver_db.complaints"
            ).fetchone()[0]
            dated_rows = connection.execute(
                "SELECT count(*) FROM silver_db.complaints WHERE date_received IS NOT NULL"
            ).fetchone()[0]
            reconciliation = {}
            for name in dimensions:
                total = connection.execute(
                    f"SELECT coalesce(sum(complaint_count), 0) FROM {name}"
                ).fetchone()[0]
                reconciliation[name] = {
                    "metric_total": total,
                    "matches_dated_silver_rows": total == dated_rows,
                }
                if total != dated_rows:
                    raise RuntimeError(f"reconciliation failed for {name}")
        finally:
            connection.close()
        manifest: dict[str, Any] = {
            "built_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "source": str(silver),
            "silver_rows": silver_rows,
            "dated_silver_rows": dated_rows,
            "tables": list(dimensions),
            "reconciliation": reconciliation,
        }
        publish_generation(temporary, manifest, gold, manifest_output)
        return manifest
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silver-db", required=True)
    parser.add_argument("--gold-db", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    rebuild_gold(args.silver_db, args.gold_db, args.manifest)
    print(current_artifact_path(args.gold_db))


if __name__ == "__main__":
    main()
