"""DuckDB bronze snapshot creation with provenance and population validation."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from meridian_assistant.publication import current_artifact_path, publish_generation

DEFAULT_TABLE = "complaints"
DEFAULT_POPULATION_CRITERIA = {
    "date_received": {"start": "2024-01-01", "end": "2025-12-31", "inclusive": True},
    "consumer_complaint_narrative": "non-empty",
}


@dataclass(frozen=True)
class BronzeManifest:
    source_path_configured: str
    source_path_absolute: str
    source_url: str | None
    acquisition_method: str | None
    source_version: str | None
    retrieval_snapshot_at: str | None
    table: str
    schema: list[dict[str, Any]]
    row_count: int
    distinct_complaint_id_count: int
    narrative_count: int
    date_received_min: str | None
    date_received_max: str | None
    population_observed: dict[str, int]
    population_violations: dict[str, int]
    sha256: str
    population_criteria: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _quote(identifier: str) -> str:
    if not identifier or "." in identifier or "\x00" in identifier:
        raise ValueError("table must be one non-empty identifier")
    return '"' + identifier.replace('"', '""') + '"'


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    source_path: str | Path,
    *,
    table: str = DEFAULT_TABLE,
    source_url: str | None = None,
    acquisition_method: str | None = "local_duckdb_snapshot",
    source_version: str | None = None,
    snapshot_at: datetime | None = None,
    population_criteria: dict[str, Any] | None = None,
) -> BronzeManifest:
    """Profile a DuckDB snapshot without modifying it, including population checks."""
    configured = str(source_path)
    path = Path(source_path).expanduser().resolve(strict=True)
    criteria = dict(population_criteria or DEFAULT_POPULATION_CRITERIA)
    date_criteria = criteria.get("date_received")
    if not isinstance(date_criteria, dict) or date_criteria.get("inclusive") is not True:
        raise ValueError("population date_received criterion must be an inclusive date range")
    start, end = date_criteria.get("start"), date_criteria.get("end")
    if not isinstance(start, str) or not isinstance(end, str) or start > end:
        raise ValueError("population date_received criterion must have ordered ISO date bounds")
    if criteria.get("consumer_complaint_narrative") != "non-empty":
        raise ValueError("population narrative criterion must require non-empty narratives")
    quoted = _quote(table)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        schema_rows = connection.execute(f"DESCRIBE {quoted}").fetchall()
        if not schema_rows:
            raise ValueError(f"DuckDB table does not exist: {table}")
        schema = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES", "default": row[4]}
            for row in schema_rows
        ]
        names = {column["name"] for column in schema}
        required = {"complaint_id", "consumer_complaint_narrative", "date_received"}
        if missing := sorted(required - names):
            raise ValueError(f"table {table!r} is missing required columns: {', '.join(missing)}")
        narrative = "nullif(trim(cast(consumer_complaint_narrative AS VARCHAR)), '')"
        date = "try_cast(date_received AS DATE)"
        row_count, distinct_ids, narratives, minimum, maximum, bad_dates, blank_narratives = (
            connection.execute(
                f"SELECT count(*), count(DISTINCT complaint_id), "
                f"count(*) FILTER (WHERE {narrative} IS NOT NULL), min({date}), max({date}), "
                f"count(*) FILTER (WHERE {date} IS NULL OR {date} < CAST(? AS DATE) "
                f"OR {date} > CAST(? AS DATE)), "
                f"count(*) FILTER (WHERE {narrative} IS NULL) FROM {quoted}",
                [start, end],
            ).fetchone()
        )
    finally:
        connection.close()
    observed = {
        "date_received_in_range_count": row_count - bad_dates,
        "nonblank_narrative_count": narratives,
    }
    violations = {
        "date_received_out_of_range_or_invalid_count": bad_dates,
        "blank_narrative_count": blank_narratives,
    }
    return BronzeManifest(
        configured,
        str(path),
        source_url,
        acquisition_method,
        source_version,
        _timestamp(snapshot_at),
        table,
        schema,
        row_count,
        distinct_ids,
        narratives,
        minimum.isoformat() if minimum else None,
        maximum.isoformat() if maximum else None,
        observed,
        violations,
        sha256_file(path),
        criteria,
    )


def create_bronze(
    source_path: str | Path,
    bronze_db: str | Path,
    manifest_path: str | Path,
    *,
    allow_population_violations: bool = False,
    **manifest_args: Any,
) -> BronzeManifest:
    """Copy then validate a snapshot, publishing DB and manifest as one generation."""
    source = Path(source_path).expanduser().resolve(strict=True)
    destination = Path(bronze_db).expanduser().resolve()
    manifest_output = Path(manifest_path).expanduser().resolve()
    if len({source, destination, manifest_output}) != 3:
        raise ValueError("source, bronze database, and manifest must not overlap")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    Path(temp_name).unlink()
    try:
        shutil.copyfile(source, temp_name)
        validated = build_manifest(temp_name, **manifest_args)
        manifest = replace(
            validated,
            source_path_configured=str(source_path),
            source_path_absolute=str(source),
        )
        if any(manifest.population_violations.values()) and not allow_population_violations:
            raise ValueError(
                f"snapshot violates declared population criteria: {manifest.population_violations}"
            )
        publish_generation(Path(temp_name), manifest.to_dict(), destination, manifest_output)
        return manifest
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--bronze-db", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--source-url")
    parser.add_argument("--acquisition-method", default="local_duckdb_snapshot")
    parser.add_argument("--source-version")
    parser.add_argument(
        "--snapshot-at", help="ISO-8601 retrieval/snapshot timestamp; omitted if unavailable"
    )
    parser.add_argument(
        "--allow-population-violations",
        action="store_true",
        help="publish a nonconforming snapshot only when intentionally auditing an exception",
    )
    args = parser.parse_args(argv)
    timestamp = (
        datetime.fromisoformat(args.snapshot_at.replace("Z", "+00:00"))
        if args.snapshot_at
        else None
    )
    create_bronze(
        args.source,
        args.bronze_db,
        args.manifest,
        table=args.table,
        source_url=args.source_url,
        acquisition_method=args.acquisition_method,
        source_version=args.source_version,
        snapshot_at=timestamp,
        allow_population_violations=args.allow_population_violations,
    )
    print(current_artifact_path(args.bronze_db))


if __name__ == "__main__":
    main()
