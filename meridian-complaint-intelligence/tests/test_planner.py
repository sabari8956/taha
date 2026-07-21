from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from meridian_assistant.planner import plan_question

QUESTIONS = {
    item["id"]: item["question"]
    for item in json.loads((Path(__file__).parents[1] / "data" / "questions.json").read_text())
}


@pytest.mark.parametrize(
    (
        "question_id",
        "mode",
        "profile",
        "operation",
        "company",
        "product",
        "state",
        "start",
        "end",
        "auto_abstention",
        "relief_abstention",
    ),
    [
        (
            "q01",
            "combined",
            "ranked_issues",
            "common_sub_issues",
            None,
            "Debt collection",
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q02",
            "combined",
            "ranked_issues",
            "common_issues",
            "Cash App",
            None,
            None,
            date(2025, 1, 1),
            date(2026, 1, 1),
            False,
            False,
        ),
        (
            "q03",
            "combined",
            "structured_summary",
            "top_volume_timely_response",
            None,
            None,
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q04",
            "combined",
            "structured_summary",
            "aggregate",
            None,
            None,
            None,
            date(2023, 3, 1),
            date(2023, 4, 1),
            False,
            False,
        ),
        (
            "q05",
            "combined",
            "ranked_issues",
            "common_sub_issues",
            None,
            "Credit reporting or other personal consumer reports",
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q06",
            "narrative",
            "narrative_examples",
            None,
            "Zelle",
            None,
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q07",
            "structured",
            "quarter_drivers",
            "quarter_comparison_with_issue_drivers",
            None,
            None,
            None,
            date(2025, 4, 1),
            date(2025, 10, 1),
            False,
            False,
        ),
        (
            "q08",
            "combined",
            "ranked_issues",
            "common_issues",
            "MOHELA",
            None,
            None,
            None,
            None,
            False,
            False,
        ),
        ("q09", "narrative", "structured_summary", None, None, None, None, None, None, True, False),
        (
            "q10",
            "combined",
            "ranked_issues",
            "common_issues",
            None,
            "Mortgage",
            "CA",
            date(2025, 7, 1),
            date(2026, 1, 1),
            False,
            False,
        ),
        (
            "q11",
            "combined",
            "response_distribution",
            "response_distribution",
            "Cash App",
            None,
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q12",
            "narrative",
            "narrative_examples",
            None,
            None,
            None,
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q13",
            "narrative",
            "narrative_examples",
            None,
            "TransUnion",
            None,
            None,
            None,
            None,
            False,
            False,
        ),
        (
            "q14",
            "combined",
            "trend_drivers",
            "period_comparison_with_issue_drivers",
            None,
            "Checking or savings account",
            None,
            date(2024, 1, 1),
            date(2026, 1, 1),
            False,
            False,
        ),
        ("q15", "narrative", "structured_summary", None, None, None, None, None, None, False, True),
    ],
)
def test_exact_all_15_supplied_question_contracts(
    question_id: str,
    mode: str,
    profile: str,
    operation: str | None,
    company: str | None,
    product: str | None,
    state: str | None,
    start: date | None,
    end: date | None,
    auto_abstention: bool,
    relief_abstention: bool,
) -> None:
    result = plan_question(question_id, QUESTIONS[question_id])
    assert result.status == "planned"
    plan = result.plan
    assert plan is not None
    assert plan.mode == mode
    assert plan.synthesis_profile == profile
    assert (plan.analysis.operation if plan.analysis else None) == operation
    assert plan.company_or_brand == company
    assert plan.narrative_filters.product == product
    assert plan.narrative_filters.state == state
    assert plan.requested_start == start
    assert plan.requested_end == end
    assert plan.requires_auto_insurance_claims is auto_abstention
    assert plan.requires_verified_relief_amount is relief_abstention
    if plan.analysis:
        assert plan.analysis.filters.product == product
        assert plan.analysis.filters.state == state
    if question_id == "q03":
        assert plan.analysis and plan.analysis.cohort_size == 10
        assert plan.narrative_filters.timely_response is False
        assert plan.narrative_binding is not None
        assert plan.narrative_binding.company_from_row == 0
        assert plan.narrative_binding.company_column == "company"
    if question_id == "q07":
        assert plan.analysis and plan.analysis.baseline_start == date(2025, 4, 1)
        assert plan.analysis.comparison_start == date(2025, 7, 1)
    if question_id == "q11":
        assert plan.narrative_filters.company_response == "Closed with explanation"
    if question_id == "q05":
        assert plan.analysis
        assert (
            plan.analysis.filters.issue
            == "Problem with a company's investigation into an existing problem"
        )
        assert plan.narrative_filters.issue == plan.analysis.filters.issue
    if question_id == "q07":
        assert plan.analysis and plan.analysis.baseline_start == date(2025, 4, 1)
        assert plan.analysis.comparison_start == date(2025, 7, 1)
    if question_id == "q14":
        assert plan.analysis and plan.analysis.baseline_start == date(2024, 1, 1)
        assert plan.analysis.comparison_start == date(2025, 1, 1)


def test_q06_q12_and_q13_are_sample_bounded_without_new_taxonomy_or_aggregate() -> None:
    for question_id in ("q06", "q12", "q13"):
        plan = plan_question(question_id, QUESTIONS[question_id]).plan
        assert plan is not None
        assert plan.mode == "narrative"
        assert plan.analysis is None
        assert plan.synthesis_profile == "narrative_examples"


def test_alias_state_and_date_extraction_for_common_variant() -> None:
    result = plan_question(
        "variant", "What did California consumers describe about Zelle fraud in H2 2025?"
    )
    assert result.plan is not None
    assert result.plan.company_or_brand == "Zelle"
    assert result.plan.narrative_filters.state == "CA"
    assert result.plan.requested_start == date(2025, 7, 1)
    assert result.plan.requested_end == date(2026, 1, 1)


def test_unknown_shape_is_clear_unsupported_result() -> None:
    result = plan_question("x", "Write a poem about complaints")
    assert result.status == "unsupported"
    assert result.plan is None
    assert result.reason
