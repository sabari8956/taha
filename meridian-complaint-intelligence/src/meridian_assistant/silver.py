"""Set-based DuckDB bronze-to-silver canonicalization.

Quality report counts describe source rows, retained canonical records, duplicate rows
skipped (lowest DuckDB rowid per trimmed complaint ID wins), null fields in retained
records, and records mapped to each curated brand.  Invalid nonblank dates or timely
response flags abort the build, leaving prior silver and report outputs unchanged.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Any

import duckdb

from meridian_assistant.entities import brand_case_pairs
from meridian_assistant.publication import current_artifact_path, publish_generation

SOURCE_COLUMNS = (
    "date_received",
    "product",
    "sub_product",
    "issue",
    "sub_issue",
    "consumer_complaint_narrative",
    "company_public_response",
    "company",
    "state",
    "zip_code",
    "tags",
    "submitted_via",
    "date_sent_to_company",
    "company_response_to_consumer",
    "timely_response",
    "complaint_id",
)


def _quote(identifier: str) -> str:
    if not identifier or "." in identifier or "\x00" in identifier:
        raise ValueError("table must be one non-empty identifier")
    return '"' + identifier.replace('"', '""') + '"'


def _normal(column: str) -> str:
    return f"nullif(trim(cast({_quote(column)} AS VARCHAR)), '')"


def _date(column: str) -> str:
    # DuckDB recognizes DATE and ISO timestamps; result is an ISO DATE column.
    value = _normal(column)
    return f"try_cast({value} AS DATE)"


def _assert_source_schema(source: Path, table: str) -> None:
    connection = duckdb.connect(str(source), read_only=True)
    try:
        columns = {row[0] for row in connection.execute(f"DESCRIBE {_quote(table)}").fetchall()}
    finally:
        connection.close()
    if missing := sorted(set(SOURCE_COLUMNS) - columns):
        raise ValueError(f"table {table!r} is missing required columns: {', '.join(missing)}")


def rebuild_silver(
    source_db: str | Path,
    silver_db: str | Path,
    quality_report: str | Path,
    *,
    table: str = "complaints",
) -> dict[str, Any]:
    """Build a canonical DuckDB database atomically from a read-only DuckDB source."""
    source = Path(source_db).expanduser().resolve(strict=True)
    destination = Path(silver_db).expanduser().resolve()
    report_output = Path(quality_report).expanduser().resolve()
    if len({source, destination, report_output}) != 3:
        raise ValueError("source, silver database, and quality report must not overlap")
    _assert_source_schema(source, table)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    src_literal = str(source).replace("'", "''")
    aliases = " ".join(
        f"WHEN trim(company_key) = '{key}' THEN '{brand}'" for key, brand in brand_case_pairs()
    )
    cleaned = ",\n                ".join(
        f"{_normal(column)} AS {_quote(column)}" for column in SOURCE_COLUMNS
    )
    dates_invalid = " OR ".join(
        f"({_normal(column)} IS NOT NULL AND {_date(column)} IS NULL)"
        for column in ("date_received", "date_sent_to_company")
    )
    try:
        connection = duckdb.connect(str(temporary))
        try:
            connection.execute(f"ATTACH '{src_literal}' AS source_db (READ_ONLY)")
            source_table = f"source_db.{_quote(table)}"
            timely = _normal("timely_response")
            invalid = connection.execute(
                f"SELECT count(*) FROM {source_table} WHERE {dates_invalid} OR "
                f"({timely} IS NOT NULL AND lower({timely}) NOT IN ('yes', 'no')) OR "
                f"{_normal('complaint_id')} IS NULL"
            ).fetchone()[0]
            if invalid:
                raise ValueError(f"source contains {invalid} invalid canonical records")
            connection.execute(f"""
                CREATE TABLE complaints AS
                WITH cleaned AS (
                  SELECT rowid AS source_rowid, {cleaned} FROM {source_table}
                ), ranked AS (
                  SELECT *,
                    row_number() OVER (
                      PARTITION BY complaint_id ORDER BY source_rowid
                    ) AS duplicate_rank
                  FROM cleaned
                ), canonical AS (
                  SELECT *,
                    regexp_replace(lower(company), '[^a-z0-9]+', ' ', 'g') AS company_key
                  FROM ranked WHERE duplicate_rank = 1
                )
                SELECT complaint_id,
                  {_date("date_received")} AS date_received,
                  product, sub_product, issue, sub_issue, consumer_complaint_narrative,
                  company_public_response, company, nullif(trim(company_key), '') AS company_key,
                  CASE {aliases} END AS brand_name,
                  state, zip_code, tags, submitted_via,
                  {_date("date_sent_to_company")} AS date_sent_to_company,
                  company_response_to_consumer,
                  CASE lower(timely_response)
                    WHEN 'yes' THEN true WHEN 'no' THEN false
                  END AS timely_response
                FROM canonical
            """)
            connection.execute("CREATE INDEX complaints_company_key_idx ON complaints(company_key)")
            connection.execute(
                "CREATE INDEX complaints_date_received_idx ON complaints(date_received)"
            )
            source_rows = connection.execute(f"SELECT count(*) FROM {source_table}").fetchone()[0]
            silver_rows = connection.execute("SELECT count(*) FROM complaints").fetchone()[0]
            silver_columns = [
                row[0] for row in connection.execute("DESCRIBE complaints").fetchall()
            ]
            null_counts = {}
            for column in silver_columns:
                count = connection.execute(
                    f"SELECT count(*) FROM complaints WHERE {_quote(column)} IS NULL"
                ).fetchone()[0]
                if count:
                    null_counts[column] = count
            alias_counts = dict(
                connection.execute(
                    "SELECT brand_name, count(*) FROM complaints "
                    "WHERE brand_name IS NOT NULL GROUP BY brand_name"
                ).fetchall()
            )
        finally:
            connection.close()
        report: dict[str, Any] = {
            "source_rows": source_rows,
            "silver_rows": silver_rows,
            "duplicate_rows_skipped": source_rows - silver_rows,
            "duplicate_policy": "lowest source DuckDB rowid wins after trimming Complaint ID",
            "invalid_rows": 0,
            "null_counts": null_counts,
            "brand_alias_counts": alias_counts,
        }
        publish_generation(temporary, report, destination, report_output)
        return report
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--silver-db", required=True)
    parser.add_argument("--quality-report", required=True)
    parser.add_argument("--table", default="complaints")
    args = parser.parse_args(argv)
    rebuild_silver(args.source, args.silver_db, args.quality_report, table=args.table)
    print(current_artifact_path(args.silver_db))


if __name__ == "__main__":
    main()
