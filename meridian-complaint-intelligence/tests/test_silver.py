from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from meridian_assistant.publication import current_artifact_path  # noqa: E402
from meridian_assistant.silver import rebuild_silver  # noqa: E402

COLUMNS = (
    "date_received, product, sub_product, issue, sub_issue, "
    "consumer_complaint_narrative, company_public_response, company, state, zip_code, "
    "tags, submitted_via, date_sent_to_company, company_response_to_consumer, "
    "timely_response, complaint_id"
)


def source_db(path: Path, invalid: bool = False) -> Path:
    con = duckdb.connect(str(path))
    con.execute(
        f"CREATE TABLE complaints ({', '.join(f'{x} VARCHAR' for x in COLUMNS.split(', '))})"
    )
    rows = [
        (
            "2025-01-02T09:00:00Z",
            " Payments ",
            "",
            "Issue",
            None,
            "n",
            None,
            "BLOCK, INC.",
            "CA",
            "1",
            None,
            "Web",
            "2025-01-03",
            "Closed",
            "Yes",
            "100",
        ),
        (
            "2025-02-03",
            "Transfer",
            None,
            "Issue",
            None,
            None,
            None,
            "Early Warning Services, LLC",
            None,
            None,
            None,
            "Web",
            None,
            "Closed",
            "No",
            "101",
        ),
        (
            "2025-12-31",
            "loses",
            None,
            None,
            None,
            None,
            None,
            "Cash App",
            None,
            None,
            None,
            None,
            None,
            None,
            "No",
            " 100 ",
        ),
    ]
    if invalid:
        rows.append(("not-date",) + (None,) * 14 + ("102",))
    con.executemany(f"INSERT INTO complaints VALUES ({', '.join('?' for _ in range(16))})", rows)
    con.close()
    return path


def test_silver_is_canonical_atomic_and_reported(tmp_path: Path) -> None:
    source = source_db(tmp_path / "source.duckdb")
    silver, report_file = tmp_path / "silver.duckdb", tmp_path / "quality.json"
    report = rebuild_silver(source, silver, report_file)
    visible_silver, visible_report = (
        current_artifact_path(silver),
        current_artifact_path(report_file),
    )
    assert report == json.loads(visible_report.read_text())
    assert (
        report["source_rows"] == 3
        and report["silver_rows"] == 2
        and report["duplicate_rows_skipped"] == 1
    )
    con = duckdb.connect(str(visible_silver), read_only=True)
    assert con.execute("""
        SELECT complaint_id, date_received::VARCHAR, product, company_key, brand_name,
          timely_response
        FROM complaints ORDER BY complaint_id
    """).fetchall() == [
        ("100", "2025-01-02", "Payments", "block inc", "Cash App", True),
        ("101", "2025-02-03", "Transfer", "early warning services llc", "Zelle", False),
    ]
    con.close()
    old_database, old_report = visible_silver.read_bytes(), visible_report.read_bytes()
    with pytest.raises(ValueError, match="invalid"):
        rebuild_silver(source_db(tmp_path / "invalid.duckdb", True), silver, report_file)
    assert visible_silver.read_bytes() == old_database
    assert visible_report.read_bytes() == old_report


def test_silver_overlap_is_rejected(tmp_path: Path) -> None:
    source = source_db(tmp_path / "source.duckdb")
    with pytest.raises(ValueError, match="overlap"):
        rebuild_silver(source, source, tmp_path / "quality.json")
    with pytest.raises(ValueError, match="overlap"):
        rebuild_silver(source, tmp_path / "silver.duckdb", source)
