from __future__ import annotations

import importlib.util
import inspect
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_rag_index", Path(__file__).parents[1] / "scripts" / "build_rag_index.py"
)
assert SPEC and SPEC.loader
build_rag_index = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_rag_index)


class FakeCollection:
    def __init__(self, count_offset: int = 0) -> None:
        self.rows: list[str] = []
        self.count_offset = count_offset

    def upsert(self, *, ids, **kwargs) -> None:
        self.rows.extend(ids)

    def count(self) -> int:
        return len(self.rows) + self.count_offset


class FakePersistentClient:
    collection = FakeCollection()

    def __init__(self, path: str) -> None:
        self.path = path

    def create_collection(self, *args, **kwargs) -> FakeCollection:
        return self.collection


def _silver(path: Path, rows: int = 2) -> Path:
    connection = duckdb.connect(str(path))
    connection.execute("""
        CREATE TABLE complaints (
          complaint_id VARCHAR, consumer_complaint_narrative VARCHAR,
          product VARCHAR, sub_product VARCHAR, issue VARCHAR, sub_issue VARCHAR,
          company VARCHAR, company_key VARCHAR, brand_name VARCHAR,
          date_received DATE, state VARCHAR, timely_response BOOLEAN,
          company_response_to_consumer VARCHAR
        )
    """)
    for index in range(rows):
        connection.execute(
            "INSERT INTO complaints VALUES (?, ?, 'Payments', '', 'Issue', '', "
            "'Block, Inc.', 'block inc', 'Cash App', DATE '2025-01-01', "
            "'CA', false, 'Closed')",
            [str(index + 1), f"narrative {index}"],
        )
    connection.close()
    return path


def _configure(monkeypatch, *, count_offset: int = 0) -> FakeCollection:
    collection = FakeCollection(count_offset)
    FakePersistentClient.collection = collection
    monkeypatch.setattr(build_rag_index.chromadb, "PersistentClient", FakePersistentClient)
    monkeypatch.setattr(build_rag_index, "_openai_embedding_client", lambda: object())
    monkeypatch.setattr(
        build_rag_index,
        "_embed",
        lambda client, rows, model: (rows, [[0.1, 0.2] for _ in rows]),
    )
    monkeypatch.setattr(build_rag_index, "_embedding_config", lambda: ("key", "base"))
    return collection


def test_metadata_has_iso_and_numeric_date_and_omits_null_ordinal() -> None:
    row = (
        "1",
        "narrative",
        "Payments",
        "",
        "Issue",
        "",
        "Block",
        "block",
        "Cash App",
        date(2025, 1, 1),
        "CA",
        False,
        "Closed",
    )
    metadata = build_rag_index._metadata(row)
    assert metadata["date_received"] == "2025-01-01"
    assert metadata["date_received_ordinal"] == date(2025, 1, 1).toordinal()
    null_metadata = build_rag_index._metadata(row[:9] + (None,) + row[10:])
    assert null_metadata["date_received"] == ""
    assert "date_received_ordinal" not in null_metadata


def test_resume_api_is_absent_and_clean_build_is_only_contract() -> None:
    assert "resume_collection" not in inspect.signature(build_rag_index.build_index).parameters


def test_embedding_provider_configuration_uses_one_explicit_base_url(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://embeddings.example/v1")
    assert build_rag_index._embedding_config() == (
        "test-key",
        "https://embeddings.example/v1",
    )


def test_rate_limit_retry_uses_retry_after_without_network(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    class Embeddings:
        def create(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                error = build_rag_index.RateLimitError(
                    "limited",
                    response=SimpleNamespace(
                        status_code=429,
                        headers={"retry-after": "70"},
                        request=SimpleNamespace(),
                    ),
                    body=None,
                )
                raise error
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])])

    monkeypatch.setattr(build_rag_index.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(build_rag_index.time, "sleep", sleeps.append)
    rows, vectors = build_rag_index._embed(
        SimpleNamespace(embeddings=Embeddings()),
        [("1", "n", None, None, None, None, None, None, None, None, None, None, None)],
        "model",
    )
    assert calls == 2 and sleeps == [70.0]
    assert vectors == [[0.1]] and rows[0][0] == "1"


def test_clean_build_limit_and_atomic_publication(tmp_path: Path, monkeypatch) -> None:
    _configure(monkeypatch)
    index = tmp_path / "index"
    manifest = build_rag_index.build_index(_silver(tmp_path / "silver.duckdb", 3), index, limit=2)
    assert manifest["documents_indexed"] == 2
    assert manifest["eligible_silver_narratives"] == 3
    assert manifest["metadata_schema_version"] == 2
    assert "date_received_ordinal" in manifest["filters"]
    assert (index / "current").is_symlink()
    assert (index / "current" / "manifest.json").is_file()


def test_reconciliation_failure_cleans_pending_and_preserves_current(
    tmp_path: Path, monkeypatch
) -> None:
    _configure(monkeypatch, count_offset=1)
    index = tmp_path / "index"
    generations = index / ".generations"
    old = generations / "old"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text("{}")
    (index / "current").symlink_to(Path(".generations") / "old")
    with pytest.raises(RuntimeError, match="collection count"):
        build_rag_index.build_index(_silver(tmp_path / "silver.duckdb"), index)
    assert (index / "current").resolve() == old.resolve()
    assert list(index.glob(".pending-*")) == []


def test_publish_pointer_failure_does_not_replace_current(tmp_path: Path, monkeypatch) -> None:
    index = tmp_path / "index"
    pending = index / ".pending-build"
    pending.mkdir(parents=True)
    generations = index / ".generations"
    old = generations / "old"
    old.mkdir(parents=True)
    (index / "current").symlink_to(Path(".generations") / "old")
    original_replace = build_rag_index.os.replace

    def fail_pointer(source, destination):
        if Path(destination) == index / "current":
            raise OSError("pointer failure")
        return original_replace(source, destination)

    monkeypatch.setattr(build_rag_index.os, "replace", fail_pointer)
    with pytest.raises(OSError, match="pointer failure"):
        build_rag_index._publish(pending, index)
    assert (index / "current").resolve() == old.resolve()
