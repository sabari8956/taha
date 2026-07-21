"""Typed, grounded adapter for the generation-pinned Chroma narrative index."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_INDEX = Path("artifacts/rag/chroma")
DEFAULT_COLLECTION = "cfpb_complaints"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
REQUIRED_METADATA_SCHEMA_VERSION = 2


class RetrievalStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    CONFIGURATION_ERROR = "configuration_error"
    INDEX_ERROR = "index_error"


@dataclass(frozen=True)
class NarrativeFilters:
    company: str | None = None
    brand_name: str | None = None
    product: str | None = None
    sub_product: str | None = None
    issue: str | None = None
    sub_issue: str | None = None
    state: str | None = None
    timely_response: bool | None = None
    company_response: str | None = None
    start: date | None = None
    end: date | None = None

    def validate(self) -> None:
        if self.start and self.end and self.start >= self.end:
            raise ValueError("start must be before end (end is exclusive)")


@dataclass(frozen=True)
class NarrativeEvidence:
    complaint_id: str
    excerpt: str
    metadata: dict[str, str]
    distance: float
    truncated: bool


@dataclass(frozen=True)
class RetrievalResult:
    status: RetrievalStatus
    query: str
    filters: NarrativeFilters
    evidence: tuple[NarrativeEvidence, ...] = ()
    error: str | None = None
    generation: str | None = None
    manifest_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "query": self.query,
            "filters": {
                key: str(value) if value is not None else None
                for key, value in asdict(self.filters).items()
            },
            "evidence": [asdict(item) for item in self.evidence],
            "error": self.error,
            "generation": self.generation,
            "manifest_sha256": self.manifest_sha256,
        }


def chroma_where(filters: NarrativeFilters) -> dict[str, Any] | None:
    filters.validate()
    clauses: list[dict[str, Any]] = []
    if filters.company:
        clauses.append({"company": filters.company.strip()})
    if filters.brand_name:
        clauses.append({"brand_name": filters.brand_name.strip()})
    for field in ("product", "sub_product", "issue", "sub_issue", "state", "company_response"):
        value = getattr(filters, field)
        if value is not None:
            clauses.append({field: value})
    if filters.timely_response is not None:
        clauses.append({"timely_response": str(filters.timely_response).lower()})
    if filters.start:
        clauses.append({"date_received_ordinal": {"$gte": filters.start.toordinal()}})
    if filters.end:
        clauses.append({"date_received_ordinal": {"$lt": filters.end.toordinal()}})
    return None if not clauses else clauses[0] if len(clauses) == 1 else {"$and": clauses}


def evidence_from_chroma_response(rows: dict[str, Any]) -> tuple[NarrativeEvidence, ...]:
    """Convert a Chroma response without changing canonical Complaint ID strings."""
    documents = rows.get("documents", [[]])
    metadatas = rows.get("metadatas", [[]])
    distances = rows.get("distances", [[]])
    if not documents or not metadatas or not distances:
        return ()
    return tuple(
        NarrativeEvidence(
            str(metadata["complaint_id"]),
            document.split("Consumer narrative:\n", 1)[-1],
            {str(key): str(value) for key, value in metadata.items()},
            float(distance),
            str(metadata.get("narrative_truncated", "false")).lower() == "true",
        )
        for document, metadata, distance in zip(documents[0], metadatas[0], distances[0])
    )


def current_generation(index_root: Path) -> Path:
    current = index_root / "current"
    if not current.is_symlink():
        raise FileNotFoundError(f"RAG current generation is unavailable: {current}")
    generation = current.resolve(strict=True)
    generations = index_root / ".generations"
    if not generations.is_dir() or generations.is_symlink():
        raise ValueError("RAG generation root is unavailable or unsafe")
    if generation.parent != generations.resolve(strict=True):
        raise ValueError("RAG current pointer must target a direct internal generation")
    return generation


def retrieve_narratives(
    query: str,
    filters: NarrativeFilters = NarrativeFilters(),
    top_k: int = 10,
    index_root: Path = DEFAULT_INDEX,
    collection_name: str = DEFAULT_COLLECTION,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> RetrievalResult:
    """Return typed evidence; expected empty slices are distinct from index/config errors."""
    if not query.strip():
        return RetrievalResult(
            RetrievalStatus.CONFIGURATION_ERROR, query, filters, error="query must be nonblank"
        )
    if top_k < 1:
        return RetrievalResult(
            RetrievalStatus.CONFIGURATION_ERROR, query, filters, error="top_k must be positive"
        )
    try:
        where = chroma_where(filters)
        generation = current_generation(index_root)
        manifest_path = generation / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        manifest_model = manifest.get("embedding_model")
        provider = manifest.get("embedding_provider")
        base_url = manifest.get("embedding_base_url")
        if manifest.get("metadata_schema_version") != REQUIRED_METADATA_SCHEMA_VERSION:
            raise ValueError(
                "RAG index metadata schema is stale; rebuild the RAG index before retrieval"
            )
        if (
            manifest.get("collection") != collection_name
            or manifest_model != embedding_model
            or provider != "openai"
            or not isinstance(base_url, str)
            or not base_url.strip()
        ):
            raise ValueError(
                "RAG manifest is incompatible with requested collection or embedding model"
            )
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for retrieval embeddings")
        import chromadb
        from openai import OpenAI

        embedding_response = OpenAI(api_key=key, base_url=base_url).embeddings.create(
            input=[query], model=manifest_model
        )
        query_embedding = embedding_response.data[0].embedding
        collection = chromadb.PersistentClient(path=str(generation / "chroma")).get_collection(
            collection_name
        )
        rows = collection.query(
            query_embeddings=[query_embedding],
            where=where,
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        evidence = evidence_from_chroma_response(rows)
        return RetrievalResult(
            RetrievalStatus.OK if evidence else RetrievalStatus.EMPTY,
            query,
            filters,
            evidence,
            generation=generation.name,
            manifest_sha256=manifest_sha256,
        )
    except ValueError as error:
        return RetrievalResult(
            RetrievalStatus.CONFIGURATION_ERROR, query, filters, error=str(error)
        )
    except Exception as error:
        return RetrievalResult(RetrievalStatus.INDEX_ERROR, query, filters, error=str(error))
