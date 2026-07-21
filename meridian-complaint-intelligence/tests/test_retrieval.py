import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from meridian_assistant.retrieval import (
    NarrativeFilters,
    RetrievalStatus,
    chroma_where,
    current_generation,
    evidence_from_chroma_response,
    retrieve_narratives,
)


def test_filters_keep_legal_company_and_brand_constraints_separate() -> None:
    filters = NarrativeFilters(
        company=" Block, Inc. ",
        brand_name="Cash App",
        state="TX",
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
    )
    where = chroma_where(filters)
    assert {"company": "Block, Inc."} in where["$and"]
    assert {"brand_name": "Cash App"} in where["$and"]
    assert {"date_received_ordinal": {"$gte": date(2025, 1, 1).toordinal()}} in where["$and"]
    assert {"date_received_ordinal": {"$lt": date(2026, 1, 1).toordinal()}} in where["$and"]
    with pytest.raises(ValueError, match="start"):
        chroma_where(NarrativeFilters(start=date(2025, 1, 1), end=date(2025, 1, 1)))


def test_unavailable_generation_is_index_error(tmp_path: Path) -> None:
    result = retrieve_narratives("test", index_root=tmp_path)
    assert result.status == RetrievalStatus.INDEX_ERROR


def test_nonblank_and_top_k_validation(tmp_path: Path) -> None:
    assert (
        retrieve_narratives(" ", index_root=tmp_path).status == RetrievalStatus.CONFIGURATION_ERROR
    )
    assert (
        retrieve_narratives("test", top_k=0, index_root=tmp_path).status
        == RetrievalStatus.CONFIGURATION_ERROR
    )


def test_chroma_evidence_preserves_string_complaint_ids_and_excerpts() -> None:
    rows = {
        "documents": [["Complaint ID: 00123\nConsumer narrative:\nLate-fee dispute"]],
        "metadatas": [[{"complaint_id": "00123", "narrative_truncated": "true"}]],
        "distances": [[0.125]],
    }
    evidence = evidence_from_chroma_response(rows)
    assert len(evidence) == 1
    assert evidence[0].complaint_id == "00123"
    assert evidence[0].excerpt == "Late-fee dispute"
    assert evidence[0].truncated is True


def test_current_generation_accepts_internal_manifest_generation(tmp_path: Path) -> None:
    root = tmp_path / "index"
    generation = root / ".generations" / "abc"
    generation.mkdir(parents=True)
    (generation / "manifest.json").write_text("{}")
    (root / "current").symlink_to(Path(".generations") / "abc", target_is_directory=True)
    assert current_generation(root) == generation.resolve()


def test_current_generation_rejects_external_pointer(tmp_path: Path) -> None:
    root = tmp_path / "index"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "current").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        current_generation(root)


def test_retrieve_embeds_query_from_manifest_and_opens_collection_without_embedding_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "index"
    generation = root / ".generations" / "generation-1"
    generation.mkdir(parents=True)
    manifest = {
        "collection": "complaints",
        "embedding_provider": "openai",
        "embedding_model": "recorded-model",
        "embedding_base_url": "https://embeddings.example.test/v1",
        "metadata_schema_version": 2,
    }
    (generation / "manifest.json").write_text(json.dumps(manifest))
    (root / "current").symlink_to(Path(".generations") / generation.name)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    calls: dict[str, object] = {}

    class FakeEmbeddings:
        def create(self, *, input: list[str], model: str) -> SimpleNamespace:
            calls["embedding_request"] = {"input": input, "model": model}
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.25, 0.75])])

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            calls["openai"] = {"api_key": api_key, "base_url": base_url}
            self.embeddings = FakeEmbeddings()

    class FakeCollection:
        def query(self, **kwargs: object) -> dict[str, object]:
            calls["query"] = kwargs
            return {
                "documents": [["Complaint ID: 00123\nConsumer narrative:\nLate-fee dispute"]],
                "metadatas": [[{"complaint_id": "00123"}]],
                "distances": [[0.125]],
            }

    class FakePersistentClient:
        def __init__(self, *, path: str) -> None:
            calls["chroma_path"] = path

        def get_collection(self, *args: object, **kwargs: object) -> FakeCollection:
            calls["get_collection"] = {"args": args, "kwargs": kwargs}
            return FakeCollection()

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("chromadb.PersistentClient", FakePersistentClient)

    filters = NarrativeFilters(company=" Block, Inc. ", state="TX")
    result = retrieve_narratives(
        "fee dispute",
        filters=filters,
        top_k=3,
        index_root=root,
        collection_name="complaints",
        embedding_model="recorded-model",
    )

    assert result.status == RetrievalStatus.OK
    assert result.generation == generation.name
    assert result.manifest_sha256 is not None
    assert result.evidence[0].complaint_id == "00123"
    assert calls["openai"] == {
        "api_key": "test-secret",
        "base_url": "https://embeddings.example.test/v1",
    }
    assert calls["embedding_request"] == {
        "input": ["fee dispute"],
        "model": "recorded-model",
    }
    assert calls["get_collection"] == {"args": ("complaints",), "kwargs": {}}
    assert calls["query"] == {
        "query_embeddings": [[0.25, 0.75]],
        "where": {"$and": [{"company": "Block, Inc."}, {"state": "TX"}]},
        "n_results": 3,
        "include": ["documents", "metadatas", "distances"],
    }


@pytest.mark.parametrize(
    ("manifest_override", "missing_field"),
    [
        ({"embedding_provider": "cohere"}, None),
        ({"embedding_provider": {"name": "openai"}}, None),
        ({}, "embedding_provider"),
        ({"embedding_base_url": "   "}, None),
        ({"embedding_base_url": None}, None),
        ({}, "embedding_base_url"),
    ],
)
def test_invalid_embedding_manifest_fails_before_openai_or_chroma(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_override: dict[str, object],
    missing_field: str | None,
) -> None:
    root = tmp_path / "index"
    generation = root / ".generations" / "generation-1"
    generation.mkdir(parents=True)
    manifest: dict[str, object] = {
        "collection": "complaints",
        "embedding_provider": "openai",
        "embedding_model": "recorded-model",
        "embedding_base_url": "https://embeddings.example.test/v1",
        "metadata_schema_version": 2,
    }
    manifest.update(manifest_override)
    if missing_field is not None:
        manifest.pop(missing_field)
    (generation / "manifest.json").write_text(json.dumps(manifest))
    (root / "current").symlink_to(Path(".generations") / generation.name)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    def unexpected_call(*args: object, **kwargs: object) -> None:
        pytest.fail(f"invalid manifest reached external client: {args=}, {kwargs=}")

    monkeypatch.setattr("openai.OpenAI", unexpected_call)
    monkeypatch.setattr("chromadb.PersistentClient", unexpected_call)

    result = retrieve_narratives(
        "fee dispute",
        index_root=root,
        collection_name="complaints",
        embedding_model="recorded-model",
    )

    assert result.status == RetrievalStatus.CONFIGURATION_ERROR
    assert (
        result.error == "RAG manifest is incompatible with requested collection or embedding model"
    )


def test_openai_embedding_failure_is_index_error_without_api_key_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "index"
    generation = root / ".generations" / "generation-1"
    generation.mkdir(parents=True)
    manifest = {
        "collection": "complaints",
        "embedding_provider": "openai",
        "embedding_model": "recorded-model",
        "embedding_base_url": "https://embeddings.example.test/v1",
        "metadata_schema_version": 2,
    }
    (generation / "manifest.json").write_text(json.dumps(manifest))
    (root / "current").symlink_to(Path(".generations") / generation.name)
    api_key = "test-secret-not-for-errors"
    monkeypatch.setenv("OPENAI_API_KEY", api_key)

    class FailingEmbeddings:
        def create(self, *, input: list[str], model: str) -> None:
            raise RuntimeError("embedding service unavailable")

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            assert api_key == "test-secret-not-for-errors"
            assert base_url == "https://embeddings.example.test/v1"
            self.embeddings = FailingEmbeddings()

    def unexpected_chroma_call(*args: object, **kwargs: object) -> None:
        pytest.fail(f"embedding failure reached Chroma: {args=}, {kwargs=}")

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    monkeypatch.setattr("chromadb.PersistentClient", unexpected_chroma_call)

    result = retrieve_narratives(
        "fee dispute",
        index_root=root,
        collection_name="complaints",
        embedding_model="recorded-model",
    )

    assert result.status == RetrievalStatus.INDEX_ERROR
    assert result.error == "embedding service unavailable"
    assert api_key not in result.error
