from __future__ import annotations

from pathlib import Path

import meridian_assistant.api as api


def test_server_uses_semantic_planner_by_default(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class Planner:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

    monkeypatch.setattr(api, "SemanticPlanner", Planner)
    api.create_app(
        bronze_manifest=Path("missing.json"),
        questions_path=Path("missing-questions.json"),
    )
    assert observed["model"] == "gpt-5.6-luna"
    assert observed["reference_questions"] == {}
