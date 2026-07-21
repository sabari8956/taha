from __future__ import annotations

from types import SimpleNamespace

from meridian_assistant.semantic_planner import SemanticPlanner


def _payload(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "mode": "combined",
        "operation": "common_issues",
        "dimensions": ["issue"],
        "filters": {
            "company": None,
            "product": "Mortgage",
            "issue": None,
            "sub_issue": None,
            "state": "CA",
            "response_category": None,
            "start_date": "2025-07-01",
            "end_date": "2026-01-01",
        },
        "narrative": {
            "enabled": True,
            "product": "Mortgage",
            "issue": None,
            "sub_issue": None,
            "state": "CA",
            "company_response": None,
            "timely_response": None,
        },
        "company_or_brand": None,
        "requested_start": "2025-07-01",
        "requested_end": "2026-01-01",
        "baseline_start": None,
        "comparison_start": None,
        "limit": 5,
        "cohort_size": 10,
        "synthesis_profile": "ranked_issues",
        "requires_verified_relief_amount": False,
        "requires_auto_insurance_claims": False,
        "refusal_reason": None,
    }
    result.update(overrides)
    return result


class _Client:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        import json

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))]
        )


def test_semantic_planner_compiles_model_json_to_guarded_plan() -> None:
    client = _Client(_payload())
    result = SemanticPlanner(client=client)("any-id", "What are California mortgage complaints?")
    assert result.status == "planned"
    assert result.plan is not None
    assert result.plan.analysis is not None
    assert result.plan.analysis.operation == "common_issues"
    assert result.plan.analysis.filters.product == "Mortgage"
    assert result.plan.narrative_filters.state == "CA"
    assert client.kwargs["response_format"]["type"] == "json_schema"  # type: ignore[attr-defined]


def test_semantic_planner_sends_reference_questions_for_numbered_question_requests() -> None:
    client = _Client(_payload())
    planner = SemanticPlanner(client=client, reference_questions={"q09": "State Farm question"})
    assert planner("x", "answer question number nine").status == "planned"
    prompt = client.kwargs["messages"][1]["content"]  # type: ignore[attr-defined]
    assert "State Farm question" in prompt


def test_semantic_planner_rejects_unsafe_or_malformed_model_plan() -> None:
    result = SemanticPlanner(client=_Client(_payload(operation="DROP TABLE complaints")))(
        "x", "question"
    )
    assert result.status == "unsupported"
    assert "Semantic plan rejected" in (result.reason or "")


def test_semantic_planner_preserves_capability_flag_from_semantic_interpretation() -> None:
    result = SemanticPlanner(
        client=_Client(
            _payload(
                mode="narrative",
                operation=None,
                dimensions=[],
                narrative={
                    "enabled": True,
                    "product": None,
                    "issue": None,
                    "sub_issue": None,
                    "state": None,
                    "company_response": None,
                    "timely_response": None,
                },
                requires_auto_insurance_claims=True,
                synthesis_profile="narrative_examples",
            )
        )
    )("x", "State Farm auto claims")
    assert result.plan is not None
    assert result.plan.requires_auto_insurance_claims is True
