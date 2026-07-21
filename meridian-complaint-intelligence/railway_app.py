import os
from pathlib import Path

from meridian_assistant.api import create_app

app = create_app(
    gold_db=Path(os.getenv("GOLD_DB", "/data/gold/current/metrics.duckdb")),
    bronze_manifest=Path(os.getenv("BRONZE_MANIFEST", "/data/bronze/current/manifest.json")),
    rag_index=Path(os.getenv("RAG_INDEX", "/data/artifacts/rag/chroma")),
    questions_path=Path(os.getenv("QUESTIONS_PATH", "/app/data/questions.json")),
    demo_answers_path=Path(os.getenv("DEMO_ANSWERS_PATH", "/app/data/demo_answers.json")),
    enable_cors=True,
    semantic_planner_model=os.getenv("SEMANTIC_PLANNER_MODEL", "gpt-5.6-luna"),
    narrator_model=os.getenv("NARRATOR_MODEL", "gpt-5.6-luna"),
)
