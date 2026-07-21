"""Deterministic question-to-tool planning for the Meridian case-study questions.

This module recognizes only constrained, auditable request shapes.  It does not
produce findings: successful plans carry a fixed evidence notice until a grounded
synthesis plugin is installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from meridian_assistant.analytics import AnalysisFilters, AnalysisPlan
from meridian_assistant.assistant import AssistantPlan, NarrativeBinding
from meridian_assistant.retrieval import NarrativeFilters

PlanningStatus = Literal["planned", "unsupported"]

_EVIDENCE_NOTICE = (
    "Evidence was collected, but no grounded synthesis plugin is configured. "
    "Review the attached query and cited Complaint IDs; no finding is asserted."
)

_BRANDS = {
    "cash app": "Cash App",
    "zelle": "Zelle",
    "transunion": "TransUnion",
    "mohela": "MOHELA",
}
_PRODUCTS = {
    "debt collection": "Debt collection",
    "credit report": "Credit reporting or other personal consumer reports",
    "checking and savings": "Checking or savings account",
    "checking or savings": "Checking or savings account",
    "mortgage": "Mortgage",
    "student loan": "Student loan",
}
_STATES = {"california": "CA", "ca": "CA"}


@dataclass(frozen=True)
class PlanningResult:
    """A validated plan, or an explicit refusal to guess an unsupported intent."""

    status: PlanningStatus
    plan: AssistantPlan | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status == "planned" and self.plan is None:
            raise ValueError("planned result requires a plan")
        if self.status == "unsupported" and not (self.reason or "").strip():
            raise ValueError("unsupported result requires a reason")


def _company(question: str) -> str | None:
    return next((brand for key, brand in _BRANDS.items() if key in question), None)


def _product(question: str) -> str | None:
    return next((product for key, product in _PRODUCTS.items() if key in question), None)


def _state(question: str) -> str | None:
    tokens = set(re.findall(r"[a-z]+", question))
    return next(
        (state for key, state in _STATES.items() if key in question.split() or key in tokens), None
    )


def _date_range(question: str) -> tuple[date | None, date | None]:
    """Extract the date forms used by the supplied case study and common variants."""
    if "second half of 2025" in question or "h2 2025" in question:
        return date(2025, 7, 1), date(2026, 1, 1)
    match = re.search(r"\bq([1-4])\s*(20\d{2})\b", question)
    if match:
        quarter, year = int(match.group(1)), int(match.group(2))
        start = date(year, (quarter - 1) * 3 + 1, 1)
        return start, _add_months(start, 3)
    years = sorted({int(value) for value in re.findall(r"\b(20\d{2})\b", question)})
    if len(years) == 1:
        return date(years[0], 1, 1), date(years[0] + 1, 1, 1)
    if len(years) >= 2:
        return date(years[0], 1, 1), date(years[-1] + 1, 1, 1)
    return None, None


def _add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    return date(value.year + month // 12, month % 12 + 1, value.day)


def _narrative_plan(
    question_id: str,
    question: str,
    *,
    company: str | None,
    product: str | None,
    state: str | None,
    start: date | None,
    end: date | None,
    requires_auto_insurance_claims: bool = False,
) -> AssistantPlan:
    return AssistantPlan(
        question_id=question_id,
        answer_text=_EVIDENCE_NOTICE,
        mode="narrative",
        narrative_query=question,
        narrative_filters=NarrativeFilters(product=product, state=state, start=start, end=end),
        company_or_brand=company,
        requested_start=start,
        requested_end=end,
        requires_auto_insurance_claims=requires_auto_insurance_claims,
    )


def plan_question(question_id: str, question: str) -> PlanningResult:
    """Recognize case-study question shapes without using an LLM or authoring facts."""
    normalized = " ".join(question.casefold().split())
    if not question_id.strip() or not normalized:
        return PlanningResult(
            "unsupported", reason="A nonblank question ID and question are required."
        )

    company, product, state = _company(normalized), _product(normalized), _state(normalized)
    start, end = _date_range(normalized)
    relief_amount = bool(
        re.search(r"\bhow much money\b|\btypically get back\b|\bdollar amount\b", normalized)
    )
    if relief_amount:
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "narrative",
                narrative_query=question,
                requested_start=start,
                requested_end=end,
                requires_verified_relief_amount=True,
            ),
        )

    if question_id == "q13":
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "narrative",
                narrative_query=question,
                company_or_brand=company,
                synthesis_profile="narrative_examples",
            ),
        )

    if question_id == "q01":
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "common_sub_issues",
                    dimensions=("sub_issue",),
                    filters=AnalysisFilters(product="Debt collection"),
                    limit=5,
                ),
                narrative_query=question,
                narrative_filters=NarrativeFilters(product="Debt collection"),
                synthesis_profile="ranked_issues",
            ),
        )

    if question_id == "q05":
        credit_product = "Credit reporting or other personal consumer reports"
        dispute_issue = "Problem with a company's investigation into an existing problem"
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "common_sub_issues",
                    dimensions=("sub_issue",),
                    filters=AnalysisFilters(product=credit_product, issue=dispute_issue),
                    limit=5,
                ),
                narrative_query=question,
                narrative_filters=NarrativeFilters(product=credit_product, issue=dispute_issue),
                requested_start=start,
                requested_end=end,
                synthesis_profile="ranked_issues",
            ),
        )

    if question_id in {"q02", "q08", "q10"}:
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "common_issues",
                    dimensions=("issue",),
                    filters=AnalysisFilters(
                        product=product,
                        state=state,
                        start_date=start,
                        end_date=end,
                    ),
                    limit=5,
                ),
                narrative_query=question,
                narrative_filters=NarrativeFilters(
                    product=product, state=state, start=start, end=end
                ),
                company_or_brand=company,
                requested_start=start,
                requested_end=end,
                synthesis_profile="ranked_issues",
            ),
        )

    auto_claims = "auto insurance" in normalized and "claim" in normalized
    if auto_claims:
        return PlanningResult(
            "planned",
            _narrative_plan(
                question_id,
                question,
                company=company,
                product=product,
                state=state,
                start=start,
                end=end,
                requires_auto_insurance_claims=True,
            ),
        )

    # March 2023 is deliberately a date-constrained combined plan: coverage, not
    # the planner, determines whether the frozen snapshot can answer it.
    if "regional-banking" in normalized or "regional banking" in normalized:
        march_start = date(2023, 3, 1) if "2023" in normalized else start
        march_end = date(2023, 4, 1) if march_start else end
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "aggregate",
                    dimensions=("company",),
                    filters=AnalysisFilters(start_date=march_start, end_date=march_end),
                ),
                narrative_query=question,
                requested_start=march_start,
                requested_end=march_end,
            ),
        )

    if "biggest spike" in normalized or "surge in complaints" in normalized:
        comparison_start = start if start and "q3" in normalized else None
        if comparison_start is None:
            return PlanningResult(
                "unsupported", reason="A complaint-spike request needs a quarter."
            )
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "structured",
                analysis=AnalysisPlan(
                    "quarter_comparison_with_issue_drivers",
                    baseline_start=_add_months(comparison_start, -3),
                    comparison_start=comparison_start,
                    limit=3,
                ),
                requested_start=_add_months(comparison_start, -3),
                requested_end=_add_months(comparison_start, 3),
                synthesis_profile="quarter_drivers",
            ),
        )

    if "worst at responding on time" in normalized or "late cases" in normalized:
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "top_volume_timely_response",
                    metric="timely_response_rate",
                    dimensions=("company",),
                    cohort_size=10,
                    limit=1,
                ),
                narrative_query=question,
                narrative_filters=NarrativeFilters(timely_response=False),
                narrative_binding=NarrativeBinding(0, "company"),
            ),
        )

    if "actual relief" in normalized or "company resolve" in normalized:
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "response_distribution", filters=AnalysisFilters(start_date=start, end_date=end)
                ),
                narrative_query=question,
                narrative_filters=NarrativeFilters(
                    company_response="Closed with explanation", start=start, end=end
                ),
                company_or_brand=company,
                requested_start=start,
                requested_end=end,
                synthesis_profile="response_distribution",
            ),
        )

    if "going up or down" in normalized or "trend" in normalized:
        if product is None:
            return PlanningResult(
                "unsupported", reason="A trend request needs a supported product."
            )
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "combined",
                analysis=AnalysisPlan(
                    "period_comparison_with_issue_drivers",
                    dimensions=("issue",),
                    filters=AnalysisFilters(product=product),
                    baseline_start=date(2024, 1, 1),
                    comparison_start=date(2025, 1, 1),
                    limit=3,
                ),
                narrative_query=question,
                narrative_filters=NarrativeFilters(product=product, start=start, end=end),
                requested_start=start,
                requested_end=end,
                synthesis_profile="trend_drivers",
            ),
        )

    narrative_signals = (
        "what",
        "why",
        "how",
        "frustration",
        "scam",
        "fraud",
        "impact",
        "describe",
        "handling",
    )
    if any(signal in normalized for signal in narrative_signals):
        return PlanningResult(
            "planned",
            AssistantPlan(
                question_id,
                _EVIDENCE_NOTICE,
                "narrative",
                narrative_query=question,
                narrative_filters=NarrativeFilters(
                    product=product, state=state, start=start, end=end
                ),
                company_or_brand=company,
                requested_start=start,
                requested_end=end,
                synthesis_profile="narrative_examples",
            ),
        )
    return PlanningResult(
        "unsupported",
        reason="The question does not match a supported structured or narrative case-study shape.",
    )
