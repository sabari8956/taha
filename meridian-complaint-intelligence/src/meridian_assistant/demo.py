"""Small demo fallback used when the full generated data volume is unavailable."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_demo_answers(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("demo answers must be a list")
    return {item["question_id"]: item for item in payload if isinstance(item, dict) and item.get("question_id")}
