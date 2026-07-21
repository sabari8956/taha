from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from meridian_assistant.analytics import (  # noqa: E402
    AnalysisFilters,
    AnalysisPlan,
    AnalyticsPlanError,
    compile_plan,
    run_analysis,
)


def gold_db(path: Path) -> Path:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("""
            CREATE TABLE company_month_metrics AS SELECT * FROM (VALUES
              ('Acme', DATE '2025-04-01', 10, 9, 10, 0.9),
              ('Acme', DATE '2025-05-01', 5, 4, 5, 0.8),
              ('Acme', DATE '2025-06-01', 5, 4, 5, 0.8),
              ('Acme', DATE '2025-07-01', 30, 25, 30, 0.8333),
              ('Acme', DATE '2025-08-01', 20, 18, 20, 0.9),
              ('Acme', DATE '2025-09-01', 10, 9, 10, 0.9),
              ('Bravo', DATE '2025-04-01', 3, 1, 2, 0.5),
              ('Bravo', DATE '2025-07-01', 100, 90, 100, 0.9),
              ('Comparison only', DATE '2025-07-01', 50, 45, 50, 0.9),
              ('Baseline only', DATE '2025-04-01', 60, 30, 60, 0.5)
            ) AS t(company, complaint_month, complaint_count, timely_response_count,
                     timely_response_known_count, timely_response_rate)
        """)
        connection.execute("""
            CREATE TABLE company_product_month_metrics AS SELECT * FROM (VALUES
              ('Acme', 'Checking or savings account', DATE '2025-01-01', 8, 9, 10, 0.9),
              ('Acme', 'Checking or savings account', DATE '2025-02-01', 12, 8, 8, 1.0),
              ('Bravo', 'Mortgage', DATE '2025-01-01', 4, 2, 4, 0.5)
            ) AS t(company, product, complaint_month, complaint_count, timely_response_count,
                     timely_response_known_count, timely_response_rate)
        """)
        connection.execute("""
            CREATE TABLE product_month_metrics AS SELECT * FROM (VALUES
              ('Checking or savings account', DATE '2025-01-01', 8, 9, 10, 0.9),
              ('Checking or savings account', DATE '2025-02-01', 12, 8, 8, 1.0),
              ('Mortgage', DATE '2025-01-01', 4, 2, 4, 0.5)
            ) AS t(product, complaint_month, complaint_count, timely_response_count,
                     timely_response_known_count, timely_response_rate)
        """)
        connection.execute("""
            CREATE TABLE company_issue_month_metrics AS SELECT * FROM (VALUES
              ('Acme', 'Account management', DATE '2025-01-01', 10, 9, 10, 0.9)
            ) AS t(company, issue, complaint_month, complaint_count, timely_response_count,
                     timely_response_known_count, timely_response_rate)
        """)
        connection.execute("""
            CREATE TABLE company_product_state_issue_month_metrics AS SELECT * FROM (VALUES
              ('Block, Inc.', 'Mortgage', 'CA', 'Escrow', DATE '2025-07-01', 7, 7, 7, 1.0),
              ('Block, Inc.', 'Mortgage', 'CA', 'Payment', DATE '2025-07-01', 3, 3, 3, 1.0),
              ('Acme', 'Checking or savings account', 'CA', 'Access',
               DATE '2024-01-01', 10, 9, 10, 0.9),
              ('Acme', 'Checking or savings account', 'CA', 'Access',
               DATE '2025-01-01', 16, 15, 16, 0.9375),
              ('Acme', 'Checking or savings account', 'CA', 'Fees',
               DATE '2024-01-01', 4, 4, 4, 1.0),
              ('Acme', 'Checking or savings account', 'CA', 'Fees',
               DATE '2025-01-01', 2, 2, 2, 1.0)
            ) AS t(company, product, state, issue, complaint_month, complaint_count,
                     timely_response_count, timely_response_known_count, timely_response_rate)
        """)
        connection.execute("""
            CREATE TABLE company_response_month_metrics AS SELECT * FROM (VALUES
              ('Block, Inc.', 'Closed with monetary relief', DATE '2025-01-01', 7, 6, 7, 0.857),
              ('Block, Inc.', 'Closed with explanation', DATE '2025-01-01', 13, 10, 12, 0.833),
              ('Block, Inc.', 'Closed with monetary relief', DATE '2025-02-01', 3, 3, 3, 1.0)
            ) AS t(company, company_response_to_consumer, complaint_month, complaint_count,
                     timely_response_count, timely_response_known_count, timely_response_rate)
        """)
    finally:
        connection.close()
    return path


def test_rate_ranking_uses_known_denominator_and_parameterized_filters(
    tmp_path: Path,
) -> None:
    result = run_analysis(
        AnalysisPlan(
            operation="aggregate",
            metric="timely_response_rate",
            dimensions=("company",),
            filters=AnalysisFilters(company="Acme"),
            order_by="timely_response_rate",
            direction="asc",
        ),
        gold_db(tmp_path / "gold.duckdb"),
    )
    assert result.rows[0] == {"company": "Acme", "timely_response_rate": 0.8625}
    assert 'sum("timely_response_count")::DOUBLE' in result.executed_sql
    assert "SELECT *" not in result.executed_sql


def test_q11_response_distribution_and_q14_month_trend(tmp_path: Path) -> None:
    database = gold_db(tmp_path / "gold.duckdb")
    responses = run_analysis(
        AnalysisPlan(
            operation="response_distribution",
            dimensions=(),
            filters=AnalysisFilters(company="Block, Inc."),
            limit=10,
        ),
        database,
    )
    assert responses.rows[0] == {
        "company_response_to_consumer": "Closed with explanation",
        "complaint_count": 13,
        "total_complaints": 23,
        "share": 13 / 23,
    }
    assert responses.rows[1]["complaint_count"] == 10
    assert sum(row["share"] for row in responses.rows) == pytest.approx(1.0)
    trend = run_analysis(
        AnalysisPlan(
            operation="trend",
            dimensions=("product", "complaint_month"),
            filters=AnalysisFilters(product="Checking or savings account"),
        ),
        database,
    )
    assert [row["complaint_month"] for row in trend.rows] == [
        date(2025, 1, 1),
        date(2025, 2, 1),
    ]
    assert [row["complaint_count"] for row in trend.rows] == [8, 12]
    assert 'ORDER BY "complaint_month" ASC' in trend.executed_sql


def test_q03_worst_rate_is_selected_only_inside_top_volume_cohort(tmp_path: Path) -> None:
    database = gold_db(tmp_path / "gold.duckdb")
    connection = duckdb.connect(str(database))
    try:
        for index in range(8):
            connection.execute(
                "INSERT INTO company_month_metrics VALUES (?, DATE '2025-10-01', ?, ?, ?, ?)",
                [
                    f"Volume {index}",
                    20 + index,
                    18 + index,
                    20 + index,
                    (18 + index) / (20 + index),
                ],
            )
        connection.execute(
            "INSERT INTO company_month_metrics VALUES "
            "('Tiny worst', DATE '2025-10-01', 1, 0, 1, 0.0)"
        )
    finally:
        connection.close()
    result = run_analysis(
        AnalysisPlan(operation="top_volume_timely_response", cohort_size=10), database
    )
    assert result.rows[0]["company"] == "Baseline only"
    assert result.rows[0]["complaint_count"] == 60
    assert result.rows[0]["timely_response_known_count"] == 60
    assert "WITH company_scope AS" in result.executed_sql
    assert "Tiny worst" not in str(result.rows)


def test_q3_vs_q2_company_spike(tmp_path: Path) -> None:
    result = run_analysis(
        AnalysisPlan(
            operation="quarter_comparison",
            baseline_start=date(2025, 4, 1),
            comparison_start=date(2025, 7, 1),
            limit=4,
        ),
        gold_db(tmp_path / "gold.duckdb"),
    )
    assert result.rows[0] == {
        "company": "Bravo",
        "baseline_complaints": 3,
        "comparison_complaints": 100,
        "absolute_increase": 97,
    }
    assert result.rows[1] == {
        "company": "Comparison only",
        "baseline_complaints": 0,
        "comparison_complaints": 50,
        "absolute_increase": 50,
    }
    assert result.rows[-1] == {
        "company": "Baseline only",
        "baseline_complaints": 60,
        "comparison_complaints": 0,
        "absolute_increase": -60,
    }
    assert "DATE '2025-04-01'" in result.executed_sql


def test_zero_known_timely_responses_return_null_rate(tmp_path: Path) -> None:
    database = gold_db(tmp_path / "gold.duckdb")
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "INSERT INTO company_month_metrics VALUES ('Unknown', DATE '2025-04-01', 2, 0, 0, NULL)"
        )
    finally:
        connection.close()
    result = run_analysis(
        AnalysisPlan(
            operation="aggregate",
            metric="timely_response_rate",
            dimensions=("company",),
            filters=AnalysisFilters(company="Unknown"),
        ),
        database,
    )
    assert result.rows == [{"company": "Unknown", "timely_response_rate": None}]


def test_common_issues_and_period_drivers(tmp_path: Path) -> None:
    database = gold_db(tmp_path / "gold.duckdb")
    issues = run_analysis(
        AnalysisPlan(
            "common_issues",
            dimensions=("issue",),
            filters=AnalysisFilters(product="Mortgage", state="CA"),
            limit=1,
        ),
        database,
    )
    assert issues.rows == [
        {"issue": "Escrow", "complaint_count": 7, "total_complaints": 10, "share": 0.7}
    ]
    comparison = run_analysis(
        AnalysisPlan(
            "period_comparison_with_issue_drivers",
            dimensions=("issue",),
            filters=AnalysisFilters(product="Checking or savings account"),
            baseline_start=date(2024, 1, 1),
            comparison_start=date(2025, 1, 1),
            limit=2,
        ),
        database,
    )
    assert comparison.rows[0]["row_type"] == "total"
    assert comparison.rows[0]["absolute_change"] == 4
    assert [row["issue"] for row in comparison.rows[1:]] == ["Access", "Fees"]


def test_q07_quarter_drivers_are_bound_to_the_structured_winner(tmp_path: Path) -> None:
    database = gold_db(tmp_path / "gold.duckdb")
    result = run_analysis(
        AnalysisPlan(
            "quarter_comparison_with_issue_drivers",
            baseline_start=date(2025, 4, 1),
            comparison_start=date(2025, 7, 1),
            limit=2,
        ),
        database,
    )
    assert result.rows[0]["row_type"] == "total"
    assert result.rows[0]["winner_company"] == "Bravo"
    assert result.rows[0]["absolute_change"] == 97
    assert all(row["row_type"] == "driver" for row in result.rows[1:])
    assert 'WHERE "company" = (SELECT "company" FROM winner)' in result.executed_sql


def test_rejects_unsupported_or_unsafe_requests() -> None:
    with pytest.raises(AnalyticsPlanError, match="start_date"):
        AnalysisFilters(start_date=date(2025, 2, 1), end_date=date(2025, 2, 1))
    with pytest.raises(AnalyticsPlanError, match="quarter_comparison requires"):
        AnalysisPlan(operation="quarter_comparison")
    with pytest.raises(AnalyticsPlanError, match="adjacent three-month"):
        AnalysisPlan(
            operation="quarter_comparison",
            baseline_start=date(2025, 1, 1),
            comparison_start=date(2025, 7, 1),
        )
    with pytest.raises(AnalyticsPlanError, match="first day"):
        AnalysisPlan(
            operation="quarter_comparison",
            baseline_start=date(2025, 4, 2),
            comparison_start=date(2025, 7, 1),
        )
    with pytest.raises(AnalyticsPlanError, match="all products"):
        compile_plan(
            AnalysisPlan(
                operation="quarter_comparison",
                baseline_start=date(2025, 4, 1),
                comparison_start=date(2025, 7, 1),
                filters=AnalysisFilters(product="Mortgage"),
            )
        )
