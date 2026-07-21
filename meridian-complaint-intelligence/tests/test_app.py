from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

import meridian_assistant.app as app
from meridian_assistant.analytics import StructuredResult
from meridian_assistant.app import answer_question, run_batch
from meridian_assistant.assistant import AssistantDependencies, AssistantPlan
from meridian_assistant.contracts import CoverageResult, DataCapabilities, EntityResolution
from meridian_assistant.planner import PlanningResult
from meridian_assistant.retrieval import (
    NarrativeEvidence,
    NarrativeFilters,
    RetrievalResult,
    RetrievalStatus,
)


def _dependencies() -> AssistantDependencies:
    def coverage(capabilities: DataCapabilities, **_: object) -> CoverageResult:
        return CoverageResult(True, None, capabilities.snapshot_start, capabilities.snapshot_end)

    def resolve(value: str) -> EntityResolution:
        return EntityResolution(value, "Block, Inc.", "Cash App", "curated_alias")

    def analysis(_: object) -> StructuredResult:
        return StructuredResult([{"company": "Block, Inc."}], "SELECT 1", (), "fake.duckdb")

    def retrieval(query: str, filters: NarrativeFilters, top_k: int) -> RetrievalResult:
        metadata = {
            key: str(value).lower() if isinstance(value, bool) else str(value)
            for key, value in {
                "company": filters.company,
                "brand_name": filters.brand_name,
                "product": filters.product,
                "state": filters.state,
                "timely_response": filters.timely_response,
                "date_received": filters.start.isoformat() if filters.start else None,
            }.items()
            if value is not None
        }
        return RetrievalResult(
            RetrievalStatus.OK,
            query,
            filters,
            (NarrativeEvidence("42", "excerpt", metadata, 0.1, False),),
            generation="generation-1",
            manifest_sha256="a" * 64,
        )

    return AssistantDependencies(
        DataCapabilities("2024-01-01", "2025-12-31", True), coverage, resolve, analysis, retrieval
    )


def test_batch_writes_answers_compatible_records_and_per_question_traces(tmp_path: Path) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps(
            [
                {
                    "id": "q07",
                    "question": (
                        "Which company had the biggest spike in complaints in Q3 2025, "
                        "and what was driving it?"
                    ),
                },
                {
                    "id": "q11",
                    "question": (
                        "When people complain about Cash App, how often does the company "
                        "resolve it with actual relief?"
                    ),
                },
            ]
        )
    )
    destination, answers = run_batch(
        questions, tmp_path / "runs", run_id="test-run", dependencies_factory=_dependencies
    )
    assert destination == tmp_path / "runs" / "test-run"
    payload = json.loads((destination / "answers.json").read_text())
    traces = json.loads((destination / "tool-traces.json").read_text())
    assert payload == [answer.to_dict() for answer in answers]
    assert set(traces) == {"q07", "q11"}
    assert all(
        set(record) == {"question_id", "answer", "citations", "query", "abstained"}
        for record in payload
    )
    assert traces["q07"]["steps"] == [
        "coverage_check",
        "run_structured_analysis",
        "synthesize",
        "validate_draft",
    ]
    evidence = json.loads((destination / "evidence.json").read_text())
    assert set(evidence) == {"q07", "q11"}
    assert evidence["q07"]["structured"]["executed_sql"] == answers[0].query
    assert evidence["q07"]["provenance"]["rag_generation"] is None
    assert evidence["q07"]["evidence_sha256"] == traces["q07"]["evidence_sha256"]


def test_batch_rejects_and_preserves_preexisting_empty_destination(tmp_path: Path) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Why are complaints going up?"}]))
    destination = tmp_path / "runs" / "occupied"
    destination.mkdir(parents=True)

    with pytest.raises(FileExistsError, match="run artifact directory already exists"):
        run_batch(
            questions,
            tmp_path / "runs",
            run_id="occupied",
            dependencies_factory=_dependencies,
        )

    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_batch_rejects_and_preserves_preexisting_dangling_symlink(tmp_path: Path) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Why are complaints going up?"}]))
    destination = tmp_path / "runs" / "occupied-link"
    destination.parent.mkdir()
    destination.symlink_to("missing-generation")

    with pytest.raises(FileExistsError, match="run artifact directory already exists"):
        run_batch(
            questions,
            tmp_path / "runs",
            run_id="occupied-link",
            dependencies_factory=_dependencies,
        )

    assert destination.is_symlink()
    assert destination.readlink() == Path("missing-generation")


def test_batch_publish_collision_preserves_destination_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Why are complaints going up?"}]))
    artifacts_root = tmp_path / "runs"
    destination = artifacts_root / "racing"

    def collide(target: str, link_name: Path) -> None:
        assert target.startswith(".racing.")
        assert link_name == destination
        destination.mkdir()
        raise FileExistsError(errno.EEXIST, "destination created by another publisher")

    monkeypatch.setattr(app.os, "symlink", collide)
    with pytest.raises(FileExistsError, match="destination created by another publisher"):
        run_batch(questions, artifacts_root, run_id="racing", dependencies_factory=_dependencies)

    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert list(artifacts_root.glob(".racing.*")) == []


def test_batch_staging_failure_leaves_no_published_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "q1", "question": "Why are complaints going up?"}]))
    calls = 0
    original_atomic_json = app._atomic_json

    def fail_while_writing(path: Path, payload: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected staging write failure")
        original_atomic_json(path, payload)

    monkeypatch.setattr(app, "_atomic_json", fail_while_writing)
    with pytest.raises(OSError, match="injected staging write failure"):
        run_batch(questions, tmp_path / "runs", run_id="failed", dependencies_factory=_dependencies)

    assert not (tmp_path / "runs" / "failed").exists()
    assert list((tmp_path / "runs").iterdir()) == []


def test_batch_rejects_duplicate_question_ids_before_execution(tmp_path: Path) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps(
            [
                {"id": "duplicate", "question": "Why are complaints going up?"},
                {"id": "duplicate", "question": "Why are complaints going down?"},
            ]
        )
    )
    executed = False

    def dependencies() -> AssistantDependencies:
        nonlocal executed
        executed = True
        return _dependencies()

    with pytest.raises(ValueError, match="duplicate question id: duplicate"):
        run_batch(questions, tmp_path / "runs", dependencies_factory=dependencies)

    assert not executed
    assert not (tmp_path / "runs").exists()


def test_retrieval_configuration_error_is_a_configuration_abstention() -> None:
    dependencies = _dependencies()

    def configuration_error(*_: object) -> RetrievalResult:
        return RetrievalResult(
            RetrievalStatus.CONFIGURATION_ERROR,
            "question",
            NarrativeFilters(),
            error="missing RAG generation",
        )

    dependencies = AssistantDependencies(
        dependencies.capabilities,
        dependencies.coverage_check,
        dependencies.company_resolver,
        dependencies.analytics_runner,
        configuration_error,
    )
    plan = AssistantPlan("q1", "evidence notice", "narrative", narrative_query="question")
    response = answer_question(
        "q1",
        "question",
        dependencies_factory=lambda: dependencies,
        planner=lambda *_: PlanningResult("planned", plan),
    )

    assert response.answer.abstained
    assert response.answer.answer == "Application configuration failure: missing RAG generation"
    assert response.trace.steps == ("configuration_error",)


def test_unsupported_question_is_an_explicit_abstention_without_real_dependencies() -> None:
    response = answer_question("unknown", "Write a poem about complaint data")
    assert response.answer.abstained
    assert response.trace.steps == ("planner_abstention",)
