"""Application adapters and batch execution for the deterministic Meridian assistant.

Live narrative requests require a compatible RAG generation at
``artifacts/rag/chroma/current`` (a direct pointer to a generation containing a
matching manifest and Chroma store).  Missing, old, or incompatible generations
are configuration failures; they are never reported as source-coverage limits.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from meridian_assistant.analytics import DEFAULT_GOLD_DB, run_analysis
from meridian_assistant.assistant import (
    AssistantDependencies,
    AssistantResponse,
    ToolTrace,
    execute_plan,
)
from meridian_assistant.contracts import AnswerRecord, CoverageResult
from meridian_assistant.coverage import (
    DEFAULT_MANIFEST,
    check_coverage,
    read_capabilities,
    resolve_company,
)
from meridian_assistant.planner import PlanningResult, plan_question
from meridian_assistant.retrieval import (
    DEFAULT_COLLECTION,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_INDEX,
    NarrativeFilters,
    RetrievalResult,
    RetrievalStatus,
    retrieve_narratives,
)
from meridian_assistant.synthesis import (
    OpenAISynthesisAdapter,
    TemplateSynthesisAdapter,
    evidence_bundle_sha256,
)


def _sha256_if_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def default_dependencies(
    *,
    gold_db: Path = DEFAULT_GOLD_DB,
    manifest_path: Path = DEFAULT_MANIFEST,
    index_root: Path = DEFAULT_INDEX,
    narrator_model: str | None = None,
) -> AssistantDependencies:
    """Create concrete adapters while deferring all filesystem/network work to execution."""
    capabilities = read_capabilities(manifest_path)

    def analytics(plan: object) -> object:
        return run_analysis(plan, gold_db)  # type: ignore[arg-type]

    def retrieval(query: str, filters: NarrativeFilters, top_k: int) -> RetrievalResult:
        return retrieve_narratives(
            query, filters, top_k, index_root, DEFAULT_COLLECTION, DEFAULT_EMBEDDING_MODEL
        )

    gold_manifest = gold_db.parent / "manifest.json"
    return AssistantDependencies(
        capabilities,
        check_coverage,
        resolve_company,
        analytics,
        retrieval,
        synthesis_adapter=OpenAISynthesisAdapter(model=narrator_model)
        if narrator_model
        else TemplateSynthesisAdapter(),
        gold_manifest_sha256=_sha256_if_file(gold_manifest),
    )


def _configuration_response(question_id: str, message: str) -> AssistantResponse:
    coverage = CoverageResult(True, None, None, None)
    answer = AnswerRecord(
        question_id,
        f"Application configuration failure: {message}",
        (),
        None,
        True,
    )
    return AssistantResponse(
        answer, ToolTrace(("configuration_error",), coverage, None, None, 0, None, 0, 0, (message,))
    )


def _unsupported_response(question_id: str, reason: str) -> AssistantResponse:
    coverage = CoverageResult(False, reason, None, None)
    return AssistantResponse(
        AnswerRecord(question_id, reason, (), None, True),
        ToolTrace(("planner_abstention",), coverage, None, None, 0, None, 0, 0, (reason,)),
    )


def answer_question(
    question_id: str,
    question: str,
    *,
    dependencies_factory: Callable[[], AssistantDependencies] = default_dependencies,
    planner: Callable[[str, str], PlanningResult] = plan_question,
) -> AssistantResponse:
    """Plan and execute one question, preserving explicit unsupported/config failures."""
    planned = planner(question_id, question)
    if planned.status == "unsupported":
        return _unsupported_response(question_id, planned.reason or "Unsupported question.")
    try:
        dependencies = dependencies_factory()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _configuration_response(question_id, str(error))
    assert planned.plan is not None
    response = execute_plan(planned.plan, dependencies, question)
    # Retrieval validates the generation pointer and manifest lazily, after
    # coverage. Reclassify that operational condition so it cannot be confused
    # with an unsupported source claim.
    if response.trace.retrieval_status is RetrievalStatus.CONFIGURATION_ERROR:
        return _configuration_response(
            question_id,
            response.trace.limitations[0] if response.trace.limitations else "RAG unavailable",
        )
    return response


def _trace_dict(trace: ToolTrace) -> dict[str, Any]:
    result = asdict(trace)
    result["retrieval_status"] = str(trace.retrieval_status) if trace.retrieval_status else None
    return result


def _evidence_dict(response: AssistantResponse) -> dict[str, Any] | None:
    evidence = response.evidence
    if evidence is None:
        return None
    structured = None
    if evidence.structured is not None:
        structured = {
            "rows": evidence.structured.rows,
            "executed_sql": evidence.structured.executed_sql,
            "parameters": list(evidence.structured.parameters),
            "source_database": evidence.structured.source_database,
        }
    retrieval = None
    if evidence.retrieval is not None:
        retrieval = {
            "status": str(evidence.retrieval.status),
            "filters": asdict(evidence.retrieval.filters),
            "evidence": [
                {
                    "complaint_id": item.complaint_id,
                    "metadata": item.metadata,
                    "distance": item.distance,
                    "truncated": item.truncated,
                    "excerpt": item.excerpt,
                    "excerpt_sha256": hashlib.sha256(item.excerpt.encode()).hexdigest(),
                }
                for item in evidence.retrieval.evidence
            ],
        }
    return {
        "evidence_sha256": evidence_bundle_sha256(evidence),
        "question": evidence.question,
        "mode": evidence.mode,
        "coverage": asdict(evidence.coverage),
        "entity_resolution": asdict(evidence.entity_resolution)
        if evidence.entity_resolution
        else None,
        "structured": structured,
        "retrieval": retrieval,
        "provenance": asdict(evidence.provenance),
        "limitations": list(evidence.limitations),
    }


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def run_batch(
    questions_path: Path = Path("data/questions.json"),
    artifacts_root: Path = Path("artifacts/run"),
    *,
    run_id: str | None = None,
    dependencies_factory: Callable[[], AssistantDependencies] = default_dependencies,
    planner: Callable[[str, str], PlanningResult] = plan_question,
) -> tuple[Path, list[AnswerRecord]]:
    """Run all input questions through :func:`answer_question` and atomically publish results."""
    raw_questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if not isinstance(raw_questions, list):
        raise ValueError("questions JSON must be an array")
    destination_name = run_id or uuid.uuid4().hex
    valid_destination_name = destination_name and Path(destination_name).name == destination_name
    if not valid_destination_name or destination_name in {".", ".."}:
        raise ValueError("run_id must be a single directory name")
    destination = artifacts_root / destination_name
    # exists() misses dangling symlinks, which are also occupied run names.
    if os.path.lexists(destination):
        raise FileExistsError(f"run artifact directory already exists: {destination}")

    questions: list[tuple[str, str]] = []
    question_ids: set[str] = set()
    for item in raw_questions:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("question"), str)
        ):
            raise ValueError("each question must contain string id and question fields")
        question_id = item["id"]
        if question_id in question_ids:
            raise ValueError(f"duplicate question id: {question_id}")
        question_ids.add(question_id)
        questions.append((question_id, item["question"]))

    answers: list[AnswerRecord] = []
    traces: dict[str, dict[str, Any]] = {}
    evidence_records: dict[str, dict[str, Any] | None] = {}
    for question_id, question in questions:
        response = answer_question(
            question_id, question, dependencies_factory=dependencies_factory, planner=planner
        )
        answers.append(response.answer)
        traces[question_id] = _trace_dict(response.trace)
        evidence_records[question_id] = _evidence_dict(response)

    artifacts_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=artifacts_root))
    try:
        _atomic_json(staging / "answers.json", [answer.to_dict() for answer in answers])
        _atomic_json(staging / "tool-traces.json", traces)
        _atomic_json(staging / "evidence.json", evidence_records)
        # Publish a completed, immutable sibling generation through a relative
        # symlink.  Unlike rename/replace, symlink creation has no replacement
        # semantics: any destination created after the check above makes this
        # operation fail with EEXIST, preserving the competing artifact.
        os.symlink(staging.name, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination, answers
