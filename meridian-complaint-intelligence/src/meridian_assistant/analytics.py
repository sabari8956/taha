"""Guarded, read-only analytics over Meridian's gold DuckDB metrics.

This module deliberately accepts typed plans rather than model-authored SQL.  Plans
are compiled from an allowlist of gold tables, dimensions, metrics, filters, and
sort directions.  DuckDB receives parameterized values; ``executed_sql`` records
the same query with its bound values rendered for an analyst to inspect or rerun.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

import duckdb

DEFAULT_GOLD_DB = Path("gold/current/metrics.duckdb")

Operation = Literal[
    "aggregate",
    "trend",
    "quarter_comparison",
    "response_distribution",
    "top_volume_timely_response",
    "common_issues",
    "common_sub_issues",
    "period_comparison_with_issue_drivers",
    "quarter_comparison_with_issue_drivers",
]
Metric = Literal["complaint_count", "timely_response_rate"]
Direction = Literal["asc", "desc"]

_ALLOWED_DIMENSIONS = {
    "company",
    "product",
    "issue",
    "sub_issue",
    "state",
    "company_response_to_consumer",
    "complaint_month",
}
_TABLE_DIMENSIONS = {
    "company_month_metrics": {"company", "complaint_month"},
    "company_product_month_metrics": {"company", "product", "complaint_month"},
    "product_month_metrics": {"product", "complaint_month"},
    "company_issue_month_metrics": {"company", "issue", "complaint_month"},
    "product_issue_sub_issue_month_metrics": {
        "product",
        "issue",
        "sub_issue",
        "complaint_month",
    },
    "company_product_state_issue_month_metrics": {
        "company",
        "product",
        "state",
        "issue",
        "complaint_month",
    },
    "company_response_month_metrics": {
        "company",
        "company_response_to_consumer",
        "complaint_month",
    },
}


class AnalyticsPlanError(ValueError):
    """Raised when a request cannot be expressed safely against gold metrics."""


@dataclass(frozen=True)
class AnalysisFilters:
    """Hard filters supported by the current gold schema.

    ``end_date`` is exclusive. Every supplied filter must be supported by the
    selected aggregate; unsupported combinations fail closed.
    """

    company: str | None = None
    product: str | None = None
    issue: str | None = None
    sub_issue: str | None = None
    state: str | None = None
    response_category: str | None = None
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise AnalyticsPlanError("start_date must be earlier than end_date")


@dataclass(frozen=True)
class AnalysisPlan:
    """A constrained request for a gold-table calculation.

    ``aggregate`` supports count/rate grouping and rankings. ``trend`` requires a
    month dimension. ``response_distribution`` returns CFPB response categories.
    ``quarter_comparison`` compares two explicit three-month windows and ranks
    companies by absolute complaint increase (the q07 definition).
    """

    operation: Operation
    metric: Metric = "complaint_count"
    dimensions: tuple[str, ...] = ()
    filters: AnalysisFilters = field(default_factory=AnalysisFilters)
    order_by: Metric = "complaint_count"
    direction: Direction = "desc"
    limit: int = 10
    baseline_start: date | None = None
    comparison_start: date | None = None
    cohort_size: int = 10

    def __post_init__(self) -> None:
        if self.operation not in {
            "aggregate",
            "trend",
            "quarter_comparison",
            "response_distribution",
            "top_volume_timely_response",
            "common_issues",
            "common_sub_issues",
            "period_comparison_with_issue_drivers",
            "quarter_comparison_with_issue_drivers",
        }:
            raise AnalyticsPlanError(f"unsupported operation: {self.operation}")
        if self.metric not in {"complaint_count", "timely_response_rate"}:
            raise AnalyticsPlanError(f"unsupported metric: {self.metric}")
        if self.order_by not in {"complaint_count", "timely_response_rate"}:
            raise AnalyticsPlanError(f"unsupported order_by: {self.order_by}")
        if self.direction not in {"asc", "desc"}:
            raise AnalyticsPlanError("direction must be asc or desc")
        if not 1 <= self.limit <= 1_000:
            raise AnalyticsPlanError("limit must be between 1 and 1000")
        if not 1 <= self.cohort_size <= 1_000:
            raise AnalyticsPlanError("cohort_size must be between 1 and 1000")
        unknown = set(self.dimensions) - _ALLOWED_DIMENSIONS
        if unknown:
            raise AnalyticsPlanError(f"unsupported dimensions: {', '.join(sorted(unknown))}")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise AnalyticsPlanError("dimensions must not contain duplicates")
        if self.operation == "trend" and "complaint_month" not in self.dimensions:
            raise AnalyticsPlanError("trend requires complaint_month in dimensions")
        if self.operation in {
            "quarter_comparison",
            "period_comparison_with_issue_drivers",
            "quarter_comparison_with_issue_drivers",
        }:
            if self.baseline_start is None or self.comparison_start is None:
                raise AnalyticsPlanError(f"{self.operation} requires both period start dates")
        if self.operation == "quarter_comparison":
            if self.baseline_start.day != 1 or self.comparison_start.day != 1:
                raise AnalyticsPlanError("quarter start dates must be the first day of a month")
            if self.comparison_start != _add_months(self.baseline_start, 3):
                raise AnalyticsPlanError("quarter comparison requires adjacent three-month windows")
        if self.operation == "quarter_comparison_with_issue_drivers":
            assert self.baseline_start is not None and self.comparison_start is not None
            if self.baseline_start.day != 1 or self.comparison_start.day != 1:
                raise AnalyticsPlanError("quarter starts must be the first day of a month")
            if self.comparison_start != _add_months(self.baseline_start, 3):
                raise AnalyticsPlanError("quarter driver comparison requires adjacent quarters")
        if self.operation == "period_comparison_with_issue_drivers":
            assert self.baseline_start is not None and self.comparison_start is not None
            if self.baseline_start.day != 1 or self.comparison_start.day != 1:
                raise AnalyticsPlanError("period starts must be the first day of a month")
            if self.comparison_start != _add_months(self.baseline_start, 12):
                raise AnalyticsPlanError("period comparison requires adjacent one-year windows")


@dataclass(frozen=True)
class StructuredResult:
    """Read-only structured evidence, including query text an analyst can rerun."""

    rows: list[dict[str, Any]]
    executed_sql: str
    parameters: tuple[Any, ...]
    source_database: str


def _add_months(value: date, months: int) -> date:
    """Return a month-boundary date offset by ``months`` calendar months."""
    month_index = value.month - 1 + months
    return date(value.year + month_index // 12, month_index % 12 + 1, value.day)


def _choose_table(plan: AnalysisPlan) -> str:
    if plan.operation in {"quarter_comparison", "top_volume_timely_response"}:
        return "company_month_metrics"
    if plan.operation == "period_comparison_with_issue_drivers":
        return "company_product_state_issue_month_metrics"
    if plan.operation == "quarter_comparison_with_issue_drivers":
        return "company_product_state_issue_month_metrics"
    if plan.operation == "response_distribution" or plan.filters.response_category is not None:
        return "company_response_month_metrics"
    if plan.operation in {"common_issues", "common_sub_issues"}:
        requested = {"issue" if plan.operation == "common_issues" else "sub_issue"}
        for column, value in (
            ("company", plan.filters.company),
            ("product", plan.filters.product),
            ("state", plan.filters.state),
        ):
            if value is not None:
                requested.add(column)
        candidates = [
            name for name, columns in _TABLE_DIMENSIONS.items() if requested.issubset(columns)
        ]
        if not candidates:
            raise AnalyticsPlanError("common_issues filters are not jointly available")
        return min(candidates, key=lambda name: len(_TABLE_DIMENSIONS[name]))
    requested = set(plan.dimensions)
    if plan.filters.company is not None:
        requested.add("company")
    if plan.filters.product is not None:
        requested.add("product")
    if plan.filters.issue is not None:
        requested.add("issue")
    if plan.filters.sub_issue is not None:
        requested.add("sub_issue")
    candidates = [
        name for name, columns in _TABLE_DIMENSIONS.items() if requested.issubset(columns)
    ]
    if not candidates:
        raise AnalyticsPlanError(
            "requested dimensions/filters are not jointly available in a gold metric table"
        )
    # Prefer the narrowest valid table; this also prevents accidental duplicate rollups.
    return min(candidates, key=lambda name: len(_TABLE_DIMENSIONS[name]))


def _filters_sql(filters: AnalysisFilters, table_columns: set[str]) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for column, value in (
        ("company", filters.company),
        ("product", filters.product),
        ("issue", filters.issue),
        ("sub_issue", filters.sub_issue),
        ("state", filters.state),
        ("company_response_to_consumer", filters.response_category),
    ):
        if value is not None:
            if column not in table_columns:
                raise AnalyticsPlanError(f"gold table does not support {column} filtering")
            clauses.append(f'"{column}" = ?')
            parameters.append(value)
    if filters.start_date is not None:
        clauses.append('"complaint_month" >= ?')
        parameters.append(filters.start_date)
    if filters.end_date is not None:
        clauses.append('"complaint_month" < ?')
        parameters.append(filters.end_date)
    return clauses, parameters


def _metric_sql(metric: Metric) -> str:
    if metric == "complaint_count":
        return 'sum("complaint_count")'
    return 'sum("timely_response_count")::DOUBLE / nullif(sum("timely_response_known_count"), 0)'


def _render_parameter(value: Any) -> str:
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if value is None:
        return "NULL"
    return str(value)


def _render_sql(sql: str, parameters: list[Any]) -> str:
    rendered = sql
    for value in parameters:
        rendered = rendered.replace("?", _render_parameter(value), 1)
    return rendered


def _compile_common_issues(plan: AnalysisPlan, table: str) -> tuple[str, list[Any]]:
    columns = _TABLE_DIMENSIONS[table]
    dimension = "issue" if plan.operation == "common_issues" else "sub_issue"
    where, parameters = _filters_sql(plan.filters, columns)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    sql = (
        f'WITH counts AS (SELECT "{dimension}" AS "issue", sum("complaint_count") AS '
        f'"complaint_count" FROM "{table}"{where_sql} GROUP BY "{dimension}"), '
        'slice_total AS (SELECT sum("complaint_count") AS "total_complaints" FROM counts) '
        'SELECT "issue", "complaint_count", "total_complaints", '
        '"complaint_count"::DOUBLE / nullif("total_complaints", 0) AS "share" '
        'FROM counts CROSS JOIN slice_total ORDER BY "complaint_count" DESC, "issue" ASC '
        f"LIMIT {plan.limit}"
    )
    return sql, parameters


def _compile_response_distribution(plan: AnalysisPlan) -> tuple[str, list[Any]]:
    table = "company_response_month_metrics"
    where, parameters = _filters_sql(plan.filters, _TABLE_DIMENSIONS[table])
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    sql = (
        'WITH category_counts AS (SELECT "company_response_to_consumer", '
        f'sum("complaint_count") AS "complaint_count" FROM "{table}"{where_sql} '
        'GROUP BY "company_response_to_consumer"), totals AS '
        '(SELECT sum("complaint_count") AS "total_complaints" FROM category_counts) '
        'SELECT "company_response_to_consumer", "complaint_count", "total_complaints", '
        '"complaint_count"::DOUBLE / nullif("total_complaints", 0) AS "share" '
        'FROM category_counts CROSS JOIN totals ORDER BY "complaint_count" DESC, '
        '"company_response_to_consumer" ASC'
    )
    return sql, parameters


def _compile_issue_driver_comparison(
    plan: AnalysisPlan, window_months: int
) -> tuple[str, list[Any]]:
    assert plan.baseline_start is not None and plan.comparison_start is not None
    comparison_end = _add_months(plan.comparison_start, window_months)
    table = "company_product_state_issue_month_metrics"
    where, parameters = _filters_sql(plan.filters, _TABLE_DIMENSIONS[table])
    where = [clause for clause in where if '"complaint_month"' not in clause]
    parameters = [
        value
        for value in parameters
        if value not in {plan.filters.start_date, plan.filters.end_date}
    ]
    where.extend(['"complaint_month" >= ?', '"complaint_month" < ?'])
    parameters.extend([plan.baseline_start, comparison_end])
    sql = (
        f'WITH issue_counts AS (SELECT coalesce("issue", \'Unknown\') AS "issue", '
        'coalesce(sum("complaint_count") FILTER (WHERE "complaint_month" >= ? AND '
        '"complaint_month" < ?), 0) AS "baseline_complaints", '
        'coalesce(sum("complaint_count") FILTER (WHERE "complaint_month" >= ? AND '
        '"complaint_month" < ?), 0) AS "comparison_complaints" '
        f'FROM "{table}" WHERE {" AND ".join(where)} GROUP BY "issue"), '
        'drivers AS (SELECT \'driver\' AS "row_type", "issue", "baseline_complaints", '
        '"comparison_complaints", "comparison_complaints" - "baseline_complaints" AS '
        '"absolute_change", CASE WHEN "baseline_complaints" = 0 THEN NULL ELSE '
        '("comparison_complaints" - "baseline_complaints")::DOUBLE / "baseline_complaints" END '
        'AS "percentage_change" FROM issue_counts), total AS (SELECT \'total\' AS "row_type", '
        'NULL::VARCHAR AS "issue", sum("baseline_complaints") AS "baseline_complaints", '
        'sum("comparison_complaints") AS "comparison_complaints", '
        'sum("absolute_change") AS "absolute_change", CASE WHEN sum("baseline_complaints") = 0 '
        'THEN NULL ELSE sum("absolute_change")::DOUBLE / sum("baseline_complaints") END AS '
        '"percentage_change" FROM drivers) SELECT * FROM total UNION ALL SELECT * FROM '
        '(SELECT * FROM drivers ORDER BY abs("absolute_change") DESC, "issue" ASC '
        f"LIMIT {plan.limit})"
    )
    parameters.extend(
        [
            plan.baseline_start,
            plan.comparison_start,
            plan.comparison_start,
            comparison_end,
        ]
    )
    # Conditional aggregate placeholders precede WHERE placeholders in SQL.
    parameters = parameters[-4:] + parameters[:-4]
    return sql, parameters


def _compile_quarter_comparison_with_issue_drivers(plan: AnalysisPlan) -> tuple[str, list[Any]]:
    assert plan.baseline_start is not None and plan.comparison_start is not None
    comparison_end = _add_months(plan.comparison_start, 3)
    table = "company_product_state_issue_month_metrics"
    sql = (
        'WITH company_counts AS (SELECT "company", '
        'coalesce(sum("complaint_count") FILTER (WHERE "complaint_month" >= ? AND '
        '"complaint_month" < ?), 0) AS "baseline_complaints", '
        'coalesce(sum("complaint_count") FILTER (WHERE "complaint_month" >= ? AND '
        '"complaint_month" < ?), 0) AS "comparison_complaints" '
        'FROM "company_month_metrics" WHERE "complaint_month" >= ? AND "complaint_month" < ? '
        'GROUP BY "company"), winner AS (SELECT *, "comparison_complaints" - '
        '"baseline_complaints" AS "absolute_change" FROM company_counts ORDER BY '
        '"absolute_change" DESC, "company" ASC LIMIT 1), issue_counts AS (SELECT '
        'coalesce("issue", \'Unknown\') AS "issue", coalesce(sum("complaint_count") FILTER '
        '(WHERE "complaint_month" >= ? AND "complaint_month" < ?), 0) AS '
        '"baseline_complaints", coalesce(sum("complaint_count") FILTER (WHERE '
        '"complaint_month" >= ? AND "complaint_month" < ?), 0) AS "comparison_complaints" '
        f'FROM "{table}" WHERE "company" = (SELECT "company" FROM winner) AND '
        '"complaint_month" >= ? AND "complaint_month" < ? GROUP BY "issue"), drivers AS '
        '(SELECT \'driver\' AS "row_type", (SELECT "company" FROM winner) AS '
        '"winner_company", "issue", "baseline_complaints", "comparison_complaints", '
        '"comparison_complaints" - "baseline_complaints" AS "absolute_change", CASE WHEN '
        '"baseline_complaints" = 0 THEN NULL ELSE ("comparison_complaints" - '
        '"baseline_complaints")::DOUBLE / "baseline_complaints" END AS "percentage_change" '
        'FROM issue_counts), total AS (SELECT \'total\' AS "row_type", "company" AS '
        '"winner_company", NULL::VARCHAR AS "issue", "baseline_complaints", '
        '"comparison_complaints", '
        '"absolute_change", CASE WHEN "baseline_complaints" = 0 THEN NULL ELSE '
        '"absolute_change"::DOUBLE / "baseline_complaints" END AS "percentage_change" FROM winner) '
        "SELECT * FROM total UNION ALL SELECT * FROM (SELECT * FROM drivers WHERE "
        f'"absolute_change" > 0 ORDER BY "absolute_change" DESC, "issue" ASC LIMIT {plan.limit})'
    )
    parameters = [
        plan.baseline_start,
        plan.comparison_start,
        plan.comparison_start,
        comparison_end,
        plan.baseline_start,
        comparison_end,
        plan.baseline_start,
        plan.comparison_start,
        plan.comparison_start,
        comparison_end,
        plan.baseline_start,
        comparison_end,
    ]
    return sql, parameters


def _compile_standard(plan: AnalysisPlan, table: str) -> tuple[str, list[Any]]:
    columns = _TABLE_DIMENSIONS[table]
    dimensions = plan.dimensions
    if plan.operation == "response_distribution":
        dimensions = tuple(dict.fromkeys((*dimensions, "company_response_to_consumer")))
    if not dimensions:
        raise AnalyticsPlanError("aggregate requests require at least one dimension")
    if not set(dimensions).issubset(columns):
        raise AnalyticsPlanError(
            "requested dimensions are not available in the selected gold table"
        )
    where, parameters = _filters_sql(plan.filters, columns)
    quoted_dimensions = ", ".join(f'"{dimension}"' for dimension in dimensions)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    value_sql = _metric_sql(plan.metric)
    if plan.operation == "trend":
        secondary_dimensions = ", ".join(
            f'"{dimension}" ASC' for dimension in dimensions if dimension != "complaint_month"
        )
        order_sql = 'ORDER BY "complaint_month" ASC'
        if secondary_dimensions:
            order_sql += f", {secondary_dimensions}"
    else:
        order_sql = (
            f"ORDER BY {_metric_sql(plan.order_by)} {plan.direction.upper()}, "
            f"{quoted_dimensions} ASC"
        )
    sql = (
        f'SELECT {quoted_dimensions}, {value_sql} AS "{plan.metric}" '
        f'FROM "{table}"{where_sql} GROUP BY {quoted_dimensions} '
        f"{order_sql} LIMIT {plan.limit}"
    )
    return sql, parameters


def _compile_quarter_comparison(plan: AnalysisPlan) -> tuple[str, list[Any]]:
    assert plan.baseline_start is not None and plan.comparison_start is not None
    if plan.filters.product is not None or plan.filters.issue is not None:
        raise AnalyticsPlanError(
            "quarter comparison currently ranks companies across all products/issues"
        )
    comparison_end = _add_months(plan.comparison_start, 3)
    where, parameters = _filters_sql(plan.filters, _TABLE_DIMENSIONS["company_month_metrics"])
    windows = (
        '("complaint_month" >= ? AND "complaint_month" < ?) OR '
        '("complaint_month" >= ? AND "complaint_month" < ?)'
    )
    where.insert(0, f"({windows})")
    parameters[0:0] = [
        plan.baseline_start,
        plan.comparison_start,
        plan.comparison_start,
        comparison_end,
    ]
    sql = (
        'WITH filtered AS (SELECT * FROM "company_month_metrics" WHERE '
        + " AND ".join(where)
        + ') SELECT "company", '
        'coalesce(sum("complaint_count") FILTER (WHERE "complaint_month" >= ? '
        'AND "complaint_month" < ?), 0) AS "baseline_complaints", '
        'coalesce(sum("complaint_count") FILTER (WHERE "complaint_month" >= ? '
        'AND "complaint_month" < ?), 0) AS "comparison_complaints", '
        '"comparison_complaints" - "baseline_complaints" AS "absolute_increase" '
        'FROM filtered GROUP BY "company" '
        'ORDER BY "absolute_increase" DESC, "company" ASC LIMIT ' + str(plan.limit)
    )
    # Window values appear in both CTE filtering and conditional aggregation.
    parameters.extend(
        [
            plan.baseline_start,
            plan.comparison_start,
            plan.comparison_start,
            comparison_end,
        ]
    )
    return sql, parameters


def _compile_top_volume_timely_response(plan: AnalysisPlan) -> tuple[str, list[Any]]:
    if plan.filters.product is not None or plan.filters.issue is not None:
        raise AnalyticsPlanError(
            "top-volume timely-response cohort supports company/date scope only"
        )
    where, parameters = _filters_sql(plan.filters, _TABLE_DIMENSIONS["company_month_metrics"])
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    sql = (
        'WITH company_scope AS (SELECT "company", sum("complaint_count") AS '
        '"complaint_count", sum("timely_response_count") AS "timely_response_count", '
        'sum("timely_response_known_count") AS "timely_response_known_count" '
        f'FROM "company_month_metrics"{where_sql} GROUP BY "company"), '
        'volume_cohort AS (SELECT *, "timely_response_count"::DOUBLE / '
        'nullif("timely_response_known_count", 0) AS "timely_response_rate" '
        'FROM company_scope ORDER BY "complaint_count" DESC, "company" ASC LIMIT '
        f'{plan.cohort_size}) SELECT "company", "complaint_count", '
        '"timely_response_count", "timely_response_known_count", "timely_response_rate" '
        'FROM volume_cohort ORDER BY "timely_response_rate" ASC NULLS LAST, '
        '"company" ASC LIMIT 1'
    )
    return sql, parameters


def compile_plan(plan: AnalysisPlan) -> tuple[str, list[Any]]:
    """Compile a validated plan to allowlisted SQL and values; never accepts SQL input."""
    table = _choose_table(plan)
    if plan.operation == "quarter_comparison":
        return _compile_quarter_comparison(plan)
    if plan.operation == "quarter_comparison_with_issue_drivers":
        return _compile_quarter_comparison_with_issue_drivers(plan)
    if plan.operation == "top_volume_timely_response":
        return _compile_top_volume_timely_response(plan)
    if plan.operation in {"common_issues", "common_sub_issues"}:
        return _compile_common_issues(plan, table)
    if plan.operation == "response_distribution":
        return _compile_response_distribution(plan)
    if plan.operation == "period_comparison_with_issue_drivers":
        return _compile_issue_driver_comparison(plan, 12)
    return _compile_standard(plan, table)


def run_analysis(plan: AnalysisPlan, gold_db: str | Path = DEFAULT_GOLD_DB) -> StructuredResult:
    """Execute a validated plan against a read-only gold database."""
    path = Path(gold_db).expanduser().resolve(strict=True)
    if path.suffix != ".duckdb":
        raise AnalyticsPlanError("gold_db must be a DuckDB database file")
    sql, parameters = compile_plan(plan)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        cursor = connection.execute(sql, parameters)
        names = [item[0] for item in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()
    return StructuredResult(rows, _render_sql(sql, parameters), tuple(parameters), str(path))
