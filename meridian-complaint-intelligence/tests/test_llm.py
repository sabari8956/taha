from __future__ import annotations

import pytest

from meridian_assistant.llm import DEFAULT_OPENAI_MODEL, openai_client
from meridian_assistant.semantic_planner import SemanticPlanner
from meridian_assistant.synthesis import OpenAISynthesisAdapter


def test_openai_is_the_default_llm_model() -> None:
    assert DEFAULT_OPENAI_MODEL == "gpt-5.6-luna"
    assert SemanticPlanner(client=object()).model == "gpt-5.6-luna"
    assert OpenAISynthesisAdapter(client=object()).model == "gpt-5.6-luna"


def test_openai_client_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        openai_client()
