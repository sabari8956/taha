from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

import meridian_assistant.app as assistant_app
import meridian_assistant.cli as assistant_cli
from meridian_assistant.analytics import AnalysisPlan, StructuredResult
from meridian_assistant.assistant import (
    AssistantDependencies,
    AssistantPlan,
    AssistantResponse,
    ToolTrace,
)
from meridian_assistant.contracts import AnswerRecord, CoverageResult, DataCapabilities
from meridian_assistant.planner import PlanningResult
from meridian_assistant.retrieval import RetrievalResult, RetrievalStatus

COLUMNS = (
    "date_received, product, sub_product, issue, sub_issue, "
    "consumer_complaint_narrative, company_public_response, company, state, zip_code, "
    "tags, submitted_via, date_sent_to_company, company_response_to_consumer, "
    "timely_response, complaint_id"
)
PROJECT_ROOT = Path(__file__).parents[1]


def test_medallion_cli_smoke_uses_tiny_duckdb_fixture(tmp_path: Path) -> None:
    source = tmp_path / "source.duckdb"
    connection = duckdb.connect(str(source))
    definitions = ", ".join(f"{column} VARCHAR" for column in COLUMNS.split(", "))
    connection.execute(f"CREATE TABLE complaints ({definitions})")
    connection.execute("""
        INSERT INTO complaints VALUES
          ('2025-01-02', 'Payments', NULL, 'Issue', NULL, 'narrative', NULL,
           'Block, Inc.', 'CA', NULL, NULL, 'Web', NULL, 'Closed', 'Yes', '1')
    """)
    connection.close()

    environment = os.environ | {"PYTHONPATH": str(PROJECT_ROOT / "src")}
    bronze, silver, gold = (tmp_path / f"{layer}.duckdb" for layer in ("bronze", "silver", "gold"))
    commands = (
        (
            "meridian_assistant.bronze",
            "--source",
            str(source),
            "--bronze-db",
            str(bronze),
            "--manifest",
            str(tmp_path / "bronze.json"),
        ),
        (
            "meridian_assistant.silver",
            "--source",
            str(bronze.parent / "current" / bronze.name),
            "--silver-db",
            str(silver),
            "--quality-report",
            str(tmp_path / "silver.json"),
        ),
        (
            "meridian_assistant.gold",
            "--silver-db",
            str(silver.parent / "current" / silver.name),
            "--gold-db",
            str(gold),
            "--manifest",
            str(tmp_path / "gold.json"),
        ),
    )
    for module, *arguments in commands:
        subprocess.run(
            [sys.executable, "-m", module, *arguments],
            check=True,
            cwd=PROJECT_ROOT,
            env=environment,
        )

    connection = duckdb.connect(str(gold.parent / "current" / gold.name), read_only=True)
    assert connection.execute(
        "SELECT complaint_count FROM company_product_month_metrics"
    ).fetchone() == (1,)
    connection.close()


def _cli_response(question_id: str) -> AssistantResponse:
    coverage = CoverageResult(True, None, None, None)
    return AssistantResponse(
        AnswerRecord(question_id, "Grounded answer.", (42,), "SELECT 1", False),
        ToolTrace(("coverage_check",), coverage, None, "SELECT 1", 1, None, 0, 1, ()),
    )


def _structured_dependencies() -> AssistantDependencies:
    return AssistantDependencies(
        DataCapabilities("2024-01-01", "2025-12-31", True),
        coverage_check=lambda capabilities, **kwargs: CoverageResult(
            True, None, capabilities.snapshot_start, capabilities.snapshot_end
        ),
        analytics_runner=lambda plan: StructuredResult(
            [{"company": "Acme", "complaint_count": 4}], "SELECT 4", (), "gold.duckdb"
        ),
        retrieval_runner=lambda query, filters, top_k: RetrievalResult(
            RetrievalStatus.EMPTY, query, filters
        ),
    )


def _cli_planner(question_id: str, question: str) -> PlanningResult:
    return PlanningResult(
        "planned",
        AssistantPlan(
            question_id,
            mode="structured",
            analysis=AnalysisPlan("aggregate", dimensions=("company",)),
        ),
    )


def test_assistant_answer_cli_uses_real_application_path(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        assistant_cli,
        "answer_question",
        lambda question_id, question, dependencies_factory: assistant_app.answer_question(
            question_id,
            question,
            dependencies_factory=lambda: _structured_dependencies(),
            planner=_cli_planner,
        ),
    )
    assert assistant_cli.main(["answer", "--id", "q1", "--question", "question"]) == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["question_id"] == "q1"
    assert payload["query"] == "SELECT 4"
    assert payload["abstained"] is False


def test_assistant_batch_cli_publishes_real_artifacts(tmp_path, monkeypatch, capsys) -> None:
    questions = tmp_path / "questions.json"
    questions.write_text('[{"id":"q1","question":"question"}]')
    destination = tmp_path / "runs" / "run-1"
    monkeypatch.setattr(
        assistant_cli,
        "run_batch",
        lambda questions, artifacts, run_id, dependencies_factory: assistant_app.run_batch(
            questions,
            artifacts,
            run_id=run_id,
            dependencies_factory=lambda: _structured_dependencies(),
            planner=_cli_planner,
        ),
    )
    result = assistant_cli.main(
        [
            "batch",
            "--questions",
            str(questions),
            "--artifacts-root",
            str(tmp_path / "runs"),
            "--run-id",
            "run-1",
        ]
    )
    assert result == 0
    assert capsys.readouterr().out.strip() == str(destination)
    assert (destination / "answers.json").is_file()
    assert (destination / "tool-traces.json").is_file()
    assert (destination / "evidence.json").is_file()
