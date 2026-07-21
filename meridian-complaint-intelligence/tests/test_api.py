from __future__ import annotations

from meridian_assistant.api import create_app
from meridian_assistant.assistant import AssistantResponse, ToolTrace
from meridian_assistant.contracts import AnswerRecord, CoverageResult


def _response() -> AssistantResponse:
    return AssistantResponse(
        AnswerRecord("q01", "Grounded answer.", (123,), "SELECT 1", False),
        ToolTrace(
            ("coverage_check", "synthesize"),
            CoverageResult(True, None, "2024-01-01", "2025-12-31"),
            None,
            "SELECT 1",
            1,
            None,
            0,
            1,
            (),
        ),
    )


def test_healthz_is_available() -> None:
    client = create_app(answer_runner=lambda *_: _response()).test_client()
    result = client.get("/healthz")
    assert result.status_code == 200
    assert result.get_json() == {"status": "ok"}


def test_answers_returns_answer_trace_and_evidence() -> None:
    received: list[tuple[str, str]] = []

    def run(question_id: str, question: str) -> AssistantResponse:
        received.append((question_id, question))
        return _response()

    client = create_app(answer_runner=run).test_client()
    result = client.post(
        "/api/v1/answers", json={"question_id": "q01", "question": "What happened?"}
    )
    assert result.status_code == 200
    assert received == [("q01", "What happened?")]
    assert result.get_json() == {
        "answer": {
            "question_id": "q01",
            "answer": "Grounded answer.",
            "citations": [123],
            "query": "SELECT 1",
            "abstained": False,
        },
        "tool_trace": {
            "steps": ["coverage_check", "synthesize"],
            "coverage": {
                "answerable": True,
                "reason": None,
                "snapshot_start": "2024-01-01",
                "snapshot_end": "2025-12-31",
                "required_capability": None,
            },
            "resolved_company": None,
            "structured_query": "SELECT 1",
            "structured_row_count": 1,
            "retrieval_status": None,
            "retrieved_evidence_count": 0,
            "accepted_citation_count": 1,
            "limitations": [],
            "entity_resolution": None,
            "structured_parameters": [],
            "executed_narrative_filters": None,
            "synthesis_adapter": None,
            "validation": None,
            "evidence_sha256": None,
        },
        "evidence": None,
    }


def test_answers_rejects_non_object_or_invalid_fields() -> None:
    client = create_app(answer_runner=lambda *_: _response()).test_client()
    for payload, message in [
        (None, "Request body must be a JSON object."),
        ({"question": "x"}, "question_id must be a nonblank string."),
        ({"question_id": "q01"}, "question must be a nonblank string."),
        ({"question_id": " ", "question": "x"}, "question_id must be a nonblank string."),
    ]:
        result = client.post("/api/v1/answers", json=payload)
        assert result.status_code == 400
        assert result.get_json()["error"]["message"] == message


def test_answers_hides_unexpected_errors() -> None:
    def fail(*_: object) -> AssistantResponse:
        raise RuntimeError("secret implementation detail")

    client = create_app(answer_runner=fail).test_client()
    result = client.post("/api/v1/answers", json={"question_id": "q01", "question": "x"})
    assert result.status_code == 500
    assert result.get_json() == {
        "error": {"code": "internal_error", "message": "Unable to process the answer request."}
    }
