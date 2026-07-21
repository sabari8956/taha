from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from meridian_assistant.gold import rebuild_gold  # noqa: E402
from meridian_assistant.publication import current_artifact_path  # noqa: E402


def silver_db(path: Path) -> Path:
    con = duckdb.connect(str(path))
    con.execute("""
        CREATE TABLE complaints AS SELECT * FROM (VALUES
          ('1', DATE '2025-01-02', 'Acme', 'Payments', 'CA', 'Late', 'Fees', 'Closed', true),
          ('2', DATE '2025-01-15', 'Acme', 'Payments', 'CA', 'Late', 'Fees', 'Closed', false),
          ('3', NULL, 'Acme', 'Cards', 'CA', 'Other', 'Other', 'Closed', true),
          ('4', DATE '2025-01-20', 'Acme', 'Payments', 'CA', 'Late', 'Fees', 'Closed', NULL),
          ('5', DATE '2025-01-21', 'Unknown Co', 'Payments', 'TX', 'Late', 'Fees', 'Closed', NULL)
        ) AS t(
          complaint_id, date_received, company, product, state, issue, sub_issue,
          company_response_to_consumer, timely_response
        )
    """)
    con.close()
    return path


def test_gold_metrics_and_reconciliation(tmp_path: Path) -> None:
    gold, manifest = tmp_path / "gold.duckdb", tmp_path / "manifest.json"
    result = rebuild_gold(silver_db(tmp_path / "silver.duckdb"), gold, manifest)
    assert json.loads(current_artifact_path(manifest).read_text())["dated_silver_rows"] == 4
    assert all(item["matches_dated_silver_rows"] for item in result["reconciliation"].values())
    con = duckdb.connect(str(current_artifact_path(gold)), read_only=True)
    assert con.execute(
        "SELECT complaint_count, timely_response_count, timely_response_known_count, "
        "timely_response_rate FROM company_product_month_metrics WHERE company = 'Acme'"
    ).fetchall() == [(3, 1, 2, 0.5)]
    assert con.execute(
        "SELECT timely_response_rate FROM company_product_month_metrics "
        "WHERE company = 'Unknown Co'"
    ).fetchone() == (None,)
    assert len(con.execute("SHOW TABLES").fetchall()) == 7
    assert con.execute(
        "SELECT complaint_count FROM company_product_state_issue_month_metrics "
        "WHERE company = 'Acme' AND state = 'CA' AND issue = 'Late'"
    ).fetchone() == (3,)
    con.close()


def test_gold_overlap_rejected(tmp_path: Path) -> None:
    silver = silver_db(tmp_path / "silver.duckdb")
    with pytest.raises(ValueError, match="overlap"):
        rebuild_gold(silver, silver, tmp_path / "manifest.json")
    with pytest.raises(ValueError, match="overlap"):
        rebuild_gold(silver, tmp_path / "gold.duckdb", silver)
