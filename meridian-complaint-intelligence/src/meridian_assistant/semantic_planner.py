"""LLM semantic planning compiled into the assistant's guarded typed contracts.

The model interprets a question but never receives database access and never emits
SQL.  Its JSON is strictly parsed into allowlisted ``AnalysisPlan`` and
``NarrativeFilters`` values before evidence collection begins.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Protocol

from meridian_assistant.analytics import AnalysisFilters, AnalysisPlan, AnalyticsPlanError
from meridian_assistant.assistant import AssistantPlan
from meridian_assistant.llm import DEFAULT_OPENAI_MODEL, openai_client
from meridian_assistant.planner import PlanningResult
from meridian_assistant.retrieval import NarrativeFilters

_ALLOWED_OPERATIONS = {
    "aggregate",
    "trend",
    "response_distribution",
    "top_volume_timely_response",
    "common_issues",
    "common_sub_issues",
    "period_comparison_with_issue_drivers",
    "quarter_comparison_with_issue_drivers",
}
_ALLOWED_PROFILES = {
    "structured_summary",
    "ranked_issues",
    "narrative_examples",
    "sample_themes",
    "response_distribution",
    "trend_drivers",
    "quarter_drivers",
}

_SCHEMA: dict[str, Any] = {
    "name": "meridian_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "mode",
            "operation",
            "dimensions",
            "filters",
            "narrative",
            "company_or_brand",
            "requested_start",
            "requested_end",
            "baseline_start",
            "comparison_start",
            "limit",
            "cohort_size",
            "synthesis_profile",
            "requires_verified_relief_amount",
            "requires_auto_insurance_claims",
            "refusal_reason",
        ],
        "properties": {
            "mode": {"type": "string", "enum": ["structured", "narrative", "combined", "refuse"]},
            "operation": {"type": ["string", "null"]},
            "dimensions": {"type": "array", "items": {"type": "string"}},
            "filters": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "company",
                    "product",
                    "issue",
                    "sub_issue",
                    "state",
                    "response_category",
                    "start_date",
                    "end_date",
                ],
                "properties": {
                    key: {"type": ["string", "null"]}
                    for key in (
                        "company",
                        "product",
                        "issue",
                        "sub_issue",
                        "state",
                        "response_category",
                        "start_date",
                        "end_date",
                    )
                },
            },
            "narrative": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "enabled",
                    "product",
                    "issue",
                    "sub_issue",
                    "state",
                    "company_response",
                    "timely_response",
                ],
                "properties": {
                    "enabled": {"type": "boolean"},
                    "product": {"type": ["string", "null"]},
                    "issue": {"type": ["string", "null"]},
                    "sub_issue": {"type": ["string", "null"]},
                    "state": {"type": ["string", "null"]},
                    "company_response": {"type": ["string", "null"]},
                    "timely_response": {"type": ["boolean", "null"]},
                },
            },
            "company_or_brand": {"type": ["string", "null"]},
            "requested_start": {"type": ["string", "null"]},
            "requested_end": {"type": ["string", "null"]},
            "baseline_start": {"type": ["string", "null"]},
            "comparison_start": {"type": ["string", "null"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "cohort_size": {"type": "integer", "minimum": 1, "maximum": 100},
            "synthesis_profile": {"type": "string"},
            "requires_verified_relief_amount": {"type": "boolean"},
            "requires_auto_insurance_claims": {"type": "boolean"},
            "refusal_reason": {"type": ["string", "null"]},
        },
    },
}

_SYSTEM = """You are a semantic planner for a CFPB complaint assistant. Interpret the user's
question and return JSON only. Never answer the question, write SQL, invent a company/entity,
or infer unsupported facts. Available structured operations are aggregate, trend,
response_distribution, top_volume_timely_response, common_issues, common_sub_issues,
period_comparison_with_issue_drivers, and quarter_comparison_with_issue_drivers. Choose only an
operation needed by the question. Set narrative.enabled only when complaint excerpts would help.
Use ISO dates and null for omitted fields. The source has CFPB complaints, not verified dollar
payout data and not established auto-insurance-claims coverage: set the corresponding capability
flag whenever the question requests either. Companies are resolved separately; preserve an
explicit company/brand mention in company_or_brand. If no safe supported plan is possible, use
mode refuse with a concise refusal_reason."""


class _Client(Protocol):
    class chat:  # pragma: no cover - structural typing only
        ...


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class SemanticPlanner:
    """OpenAI JSON-schema planner; callers may inject a fake client for tests."""

    def __init__(
        self,
        *,
        model: str | None = None,
        client: Any | None = None,
        reference_questions: dict[str, str] | None = None,
    ) -> None:
        self.model = model or DEFAULT_OPENAI_MODEL
        self.client = client
        self.reference_questions = reference_questions or {}

    def _client(self) -> Any:
        return self.client or openai_client()

    def __call__(self, question_id: str, question: str) -> PlanningResult:
        if not question_id.strip() or not question.strip():
            return PlanningResult(
                "unsupported", reason="A nonblank question ID and question are required."
            )
        try:
            completion = self._client().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "question": question,
                                "reference_questions": self.reference_questions,
                                "instruction": (
                                    "Resolve references to a numbered question using "
                                    "reference_questions before creating the plan."
                                ),
                            }
                        ),
                    },
                ],
                response_format={"type": "json_schema", "json_schema": _SCHEMA},
                temperature=0,
            )
            content = completion.choices[0].message.content
            payload = json.loads(content) if content else None
            if not isinstance(payload, dict):
                raise ValueError("model returned no JSON object")
            return self._compile(question_id, question, payload)
        except Exception as error:
            return PlanningResult(
                "unsupported", reason=f"Semantic planning unavailable: {type(error).__name__}."
            )

    @staticmethod
    def _compile(question_id: str, question: str, value: dict[str, Any]) -> PlanningResult:
        try:
            mode = value["mode"]
            if mode == "refuse":
                reason = value.get("refusal_reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("refusal requires a reason")
                return PlanningResult("unsupported", reason=reason)
            if mode not in {"structured", "narrative", "combined"}:
                raise ValueError("invalid mode")
            operation = value["operation"]
            if mode in {"structured", "combined"} and operation not in _ALLOWED_OPERATIONS:
                raise ValueError("invalid structured operation")
            filters = value["filters"]
            analysis = None
            if mode in {"structured", "combined"}:
                analysis = AnalysisPlan(
                    operation,
                    dimensions=tuple(value["dimensions"]),
                    filters=AnalysisFilters(
                        company=filters["company"],
                        product=filters["product"],
                        issue=filters["issue"],
                        sub_issue=filters["sub_issue"],
                        state=filters["state"],
                        response_category=filters["response_category"],
                        start_date=_date(filters["start_date"]),
                        end_date=_date(filters["end_date"]),
                    ),
                    limit=value["limit"],
                    cohort_size=value["cohort_size"],
                    baseline_start=_date(value["baseline_start"]),
                    comparison_start=_date(value["comparison_start"]),
                )
            narrative = value["narrative"]
            if mode in {"narrative", "combined"} and not narrative["enabled"]:
                raise ValueError("narrative mode requires narrative evidence")
            profile = value["synthesis_profile"]
            if profile not in _ALLOWED_PROFILES:
                raise ValueError("invalid synthesis profile")
            return PlanningResult(
                "planned",
                AssistantPlan(
                    question_id=question_id,
                    mode=mode,
                    analysis=analysis,
                    narrative_query=question if narrative["enabled"] else None,
                    narrative_filters=NarrativeFilters(
                        product=narrative["product"],
                        issue=narrative["issue"],
                        sub_issue=narrative["sub_issue"],
                        state=narrative["state"],
                        company_response=narrative["company_response"],
                        timely_response=narrative["timely_response"],
                    ),
                    company_or_brand=value["company_or_brand"],
                    requested_start=_date(value["requested_start"]),
                    requested_end=_date(value["requested_end"]),
                    requires_verified_relief_amount=value["requires_verified_relief_amount"],
                    requires_auto_insurance_claims=value["requires_auto_insurance_claims"],
                    top_k=min(value["limit"], 8),
                    synthesis_profile=profile,
                ),
            )
        except (KeyError, TypeError, ValueError, AnalyticsPlanError) as error:
            return PlanningResult("unsupported", reason=f"Semantic plan rejected: {error}.")
