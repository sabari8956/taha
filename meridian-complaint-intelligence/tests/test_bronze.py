from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from meridian_assistant.bronze import build_manifest, create_bronze  # noqa: E402
from meridian_assistant.publication import current_artifact_path  # noqa: E402


def source_db(path: Path, *, violating: bool = False) -> Path:
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE complaints (
        date_received VARCHAR, consumer_complaint_narrative VARCHAR, complaint_id VARCHAR)""")
    rows = [("2025-01-03T10:00:00Z", "narrative", "1")]
    if violating:
        rows.extend([("2026-01-01", "late", "2"), ("2025-01-02", " ", "3")])
    con.executemany("INSERT INTO complaints VALUES (?, ?, ?)", rows)
    con.close()
    return path


def test_manifest_validation_provenance_and_bronze_copy(tmp_path: Path) -> None:
    source = source_db(tmp_path / "source.duckdb")
    manifest = build_manifest(
        source,
        source_url="https://example.test/api",
        acquisition_method="api export",
        source_version="v1",
        snapshot_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert (
        manifest.row_count == manifest.distinct_complaint_id_count == manifest.narrative_count == 1
    )
    assert manifest.source_url == "https://example.test/api"
    assert manifest.population_violations == {
        "date_received_out_of_range_or_invalid_count": 0,
        "blank_narrative_count": 0,
    }
    bronze, output = tmp_path / "bronze" / "snapshot.duckdb", tmp_path / "bronze" / "manifest.json"
    result = create_bronze(source, bronze, output, snapshot_at=datetime(2026, 1, 2, tzinfo=UTC))
    visible_db, visible_manifest = current_artifact_path(bronze), current_artifact_path(output)
    assert visible_db.read_bytes() == source.read_bytes()
    assert json.loads(visible_manifest.read_text()) == result.to_dict()
    assert result.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_population_violations_fail_unless_explicitly_allowed(tmp_path: Path) -> None:
    source = source_db(tmp_path / "invalid.duckdb", violating=True)
    bronze, manifest = (
        tmp_path / "bronze" / "snapshot.duckdb",
        tmp_path / "bronze" / "manifest.json",
    )
    with pytest.raises(ValueError, match="population criteria"):
        create_bronze(source, bronze, manifest)
    result = create_bronze(source, bronze, manifest, allow_population_violations=True)
    assert result.population_violations == {
        "date_received_out_of_range_or_invalid_count": 1,
        "blank_narrative_count": 1,
    }


def test_failed_publication_keeps_prior_visible_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_db(tmp_path / "source.duckdb")
    bronze, manifest = (
        tmp_path / "bronze" / "snapshot.duckdb",
        tmp_path / "bronze" / "manifest.json",
    )
    create_bronze(source, bronze, manifest)
    old_db, old_json = (
        current_artifact_path(bronze).read_bytes(),
        current_artifact_path(manifest).read_bytes(),
    )
    monkeypatch.setattr(
        "meridian_assistant.bronze.publish_generation",
        lambda *_: (_ for _ in ()).throw(OSError("fail")),
    )
    with pytest.raises(OSError, match="fail"):
        create_bronze(source, bronze, manifest)
    assert current_artifact_path(bronze).read_bytes() == old_db
    assert current_artifact_path(manifest).read_bytes() == old_json


def test_overlap_and_required_columns_rejected(tmp_path: Path) -> None:
    source = source_db(tmp_path / "source.duckdb")
    with pytest.raises(ValueError, match="overlap"):
        create_bronze(source, source, tmp_path / "manifest.json")
    bad = tmp_path / "bad.duckdb"
    con = duckdb.connect(str(bad))
    con.execute("CREATE TABLE complaints (complaint_id VARCHAR)")
    con.close()
    with pytest.raises(ValueError, match="missing required"):
        build_manifest(bad)
