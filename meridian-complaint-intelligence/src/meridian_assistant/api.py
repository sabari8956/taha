"""Flask HTTP adapter for the deterministic Meridian assistant.

The API intentionally exposes only the typed, planner-mediated answer operation;
clients cannot submit SQL, retrieval filters, or tool instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from flask import Flask, Response, jsonify, request

from meridian_assistant.app import (
    _evidence_dict,
    _trace_dict,
    answer_question,
    default_dependencies,
)
from meridian_assistant.assistant import AssistantDependencies, AssistantResponse
from meridian_assistant.coverage import read_capabilities
from meridian_assistant.planner import PlanningResult
from meridian_assistant.semantic_planner import SemanticPlanner

DEFAULT_QUESTIONS = Path("data/questions.json")

DependenciesFactory = Callable[[], AssistantDependencies]
AnswerRunner = Callable[[str, str], AssistantResponse]
Planner = Callable[[str, str], PlanningResult]


def _error(status: int, code: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": {"code": code, "message": message}}), status


def _default_answer_runner(
    *,
    gold_db: Path,
    bronze_manifest: Path,
    rag_index: Path,
    dependencies_factory: DependenciesFactory | None,
    planner: Planner,
) -> AnswerRunner:
    factory = dependencies_factory or (
        lambda: default_dependencies(
            gold_db=gold_db, manifest_path=bronze_manifest, index_root=rag_index
        )
    )

    def run(question_id: str, question: str) -> AssistantResponse:
        return answer_question(
            question_id,
            question,
            dependencies_factory=factory,
            planner=planner,
        )

    return run


def create_app(
    *,
    gold_db: Path = Path("gold/current/metrics.duckdb"),
    bronze_manifest: Path = Path("bronze/current/manifest.json"),
    rag_index: Path = Path("artifacts/rag/chroma"),
    questions_path: Path = DEFAULT_QUESTIONS,
    dependencies_factory: DependenciesFactory | None = None,
    planner: Planner | None = None,
    answer_runner: AnswerRunner | None = None,
    enable_cors: bool = False,
    semantic_planner_model: str = "gpt-5.6-luna",
    narrator_model: str | None = None,
    demo_answers_path: Path | None = None,
) -> Flask:
    """Build the Flask application with injectable dependencies for tests.

    Configuration is passed as explicit paths or environment variables handled by
    the embedding client.  This adapter never loads ``.env`` or exposes secrets.
    """
    app = Flask(__name__)
    reference_questions: dict[str, str] = {}
    if semantic_planner_model:
        try:
            raw_questions = json.loads(questions_path.read_text(encoding="utf-8"))
            if isinstance(raw_questions, list):
                reference_questions = {
                    item["id"]: item["question"]
                    for item in raw_questions
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and isinstance(item.get("question"), str)
                }
        except (OSError, json.JSONDecodeError):
            app.logger.warning("Reference questions unavailable for semantic planner")
    selected_planner = planner or SemanticPlanner(
        model=semantic_planner_model, reference_questions=reference_questions
    )
    selected_dependencies = dependencies_factory or (
        lambda: default_dependencies(
            gold_db=gold_db,
            manifest_path=bronze_manifest,
            index_root=rag_index,
            narrator_model=narrator_model or semantic_planner_model,
        )
    )
    demo_answers: dict[str, dict] = {}
    if demo_answers_path:
        try:
            demo_payload = json.loads(demo_answers_path.read_text(encoding="utf-8"))
            if isinstance(demo_payload, list):
                demo_answers = {
                    item["question_id"]: item
                    for item in demo_payload
                    if isinstance(item, dict) and isinstance(item.get("question_id"), str)
                }
        except (OSError, json.JSONDecodeError, ValueError):
            app.logger.warning("Demo answers unavailable")

    runner = answer_runner or _default_answer_runner(
        gold_db=gold_db,
        bronze_manifest=bronze_manifest,
        rag_index=rag_index,
        dependencies_factory=selected_dependencies,
        planner=selected_planner,
    )

    if enable_cors:

        @app.after_request
        def _add_cors_headers(response: Response) -> Response:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        @app.route("/api/v1/answers", methods=["OPTIONS"])
        def _answers_preflight() -> Response:
            return Response(status=204)

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})

    @app.get("/api/v1/snapshot")
    def snapshot() -> tuple[Response, int] | Response:
        try:
            capabilities = read_capabilities(bronze_manifest)
        except (OSError, ValueError, json.JSONDecodeError):
            if demo_answers:
                return jsonify({
                    "snapshot_start": "2024-01-01",
                    "snapshot_end": "2025-12-31",
                    "has_consumer_narratives": True,
                    "demo_mode": True,
                })
            return _error(503, "snapshot_unavailable", "Snapshot capabilities are unavailable.")
        return jsonify(
            {
                "snapshot_start": capabilities.snapshot_start,
                "snapshot_end": capabilities.snapshot_end,
                "has_consumer_narratives": capabilities.has_consumer_narratives,
            }
        )

    @app.get("/api/v1/questions")
    def questions() -> tuple[Response, int] | Response:
        try:
            payload = json.loads(questions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _error(503, "questions_unavailable", "Sample questions are unavailable.")
        if not isinstance(payload, list):
            return _error(503, "questions_unavailable", "Sample questions are unavailable.")
        return jsonify(payload)

    @app.post("/api/v1/answers")
    def answer() -> tuple[Response, int] | Response:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error(400, "invalid_json", "Request body must be a JSON object.")
        question_id = payload.get("question_id")
        question = payload.get("question")
        if not isinstance(question_id, str) or not question_id.strip():
            return _error(400, "invalid_request", "question_id must be a nonblank string.")
        if not isinstance(question, str) or not question.strip():
            return _error(400, "invalid_request", "question must be a nonblank string.")
        if question_id in demo_answers:
            item = demo_answers[question_id]
            return jsonify({
                "answer": {
                    "question_id": question_id,
                    "answer": item.get("answer", "Demo answer unavailable."),
                    "citations": item.get("citations", []),
                    "query": item.get("query"),
                    "abstained": item.get("abstained", False),
                },
                "tool_trace": {
                    "steps": ["plan_question", "run_analysis", "retrieve_narratives", "validate_answer"],
                    "coverage": {"answerable": True, "snapshot_start": "2024-01-01", "snapshot_end": "2025-12-31"},
                    "retrieval_status": "ok",
                    "retrieved_evidence_count": len(item.get("citations", [])),
                    "accepted_citation_count": len(item.get("citations", [])),
                    "limitations": ["Showcase demo response; generated database artifacts are not mounted."] ,
                },
                "evidence": None,
            })
        try:
            response = runner(question_id, question)
        except Exception:
            app.logger.exception("Unhandled answer request failure")
            return _error(500, "internal_error", "Unable to process the answer request.")
        return jsonify(
            {
                "answer": response.answer.to_dict(),
                "tool_trace": _trace_dict(response.trace),
                "evidence": _evidence_dict(response),
            }
        )

    return app
