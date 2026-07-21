"""Build a generation-pinned Chroma index from canonical Silver narratives.

OpenAI receives the text embedded by this command and therefore requires explicit
external-processing approval; no API key is written to manifests or source control.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import tempfile
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator

import chromadb
import duckdb
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

DEFAULT_SOURCE = Path("silver/current/complaints.duckdb")
DEFAULT_INDEX = Path("artifacts/rag/chroma")
DEFAULT_COLLECTION = "cfpb_complaints"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 100
DEFAULT_WORKERS = 4
MAX_NARRATIVE_CHARS = 6_000
MAX_EMBED_RETRIES = 8


def _embedding_config() -> tuple[str, str]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
    return key, os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


def _openai_embedding_client() -> OpenAI:
    key, base_url = _embedding_config()
    return OpenAI(api_key=key, base_url=base_url)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _document_id(complaint_id: str) -> str:
    return hashlib.sha256(complaint_id.encode()).hexdigest()


def _document(row: tuple[Any, ...]) -> str:
    complaint_id, narrative, product, sub_product, issue, sub_issue, company, *_ = row
    return "\n".join(
        [
            f"Complaint ID: {complaint_id}",
            f"Company: {company or 'Unknown'}",
            f"Product: {product or 'Unknown'}",
            f"Sub-product: {sub_product or 'Unknown'}",
            f"Issue: {issue or 'Unknown'}",
            f"Sub-issue: {sub_issue or 'Unknown'}",
            "Consumer narrative:",
            narrative[:MAX_NARRATIVE_CHARS],
        ]
    )


def _date_ordinal(value: Any) -> int | None:
    """Return a Chroma-safe ordinal for canonical Silver dates."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().toordinal()
    if isinstance(value, date):
        return value.toordinal()
    raise TypeError(f"date_received must be a date, datetime, or None; got {type(value).__name__}")


def _metadata(row: tuple[Any, ...]) -> dict[str, str | int | float | bool]:
    (
        complaint_id,
        narrative,
        product,
        sub_product,
        issue,
        sub_issue,
        company,
        company_key,
        brand_name,
        date_received,
        state,
        timely_response,
        company_response,
    ) = row
    metadata: dict[str, str | int | float | bool] = {
        "complaint_id": str(complaint_id),
        "product": str(product or ""),
        "sub_product": str(sub_product or ""),
        "issue": str(issue or ""),
        "sub_issue": str(sub_issue or ""),
        "company": str(company or ""),
        "company_key": str(company_key or ""),
        "brand_name": str(brand_name or ""),
        "date_received": str(date_received or ""),
        "state": str(state or ""),
        "timely_response": str(timely_response).lower() if timely_response is not None else "",
        "company_response": str(company_response or ""),
        "narrative_truncated": str(len(narrative) > MAX_NARRATIVE_CHARS).lower(),
    }
    ordinal = _date_ordinal(date_received)
    if ordinal is not None:
        metadata["date_received_ordinal"] = ordinal
    return metadata


def _embed(
    client: OpenAI, rows: list[tuple[Any, ...]], model: str
) -> tuple[list[tuple[Any, ...]], list[list[float]]]:
    """Embed one bounded batch, respecting temporary provider rate limits."""
    for attempt in range(MAX_EMBED_RETRIES):
        try:
            response = client.embeddings.create(input=[_document(row) for row in rows], model=model)
            return rows, [
                item.embedding for item in sorted(response.data, key=lambda item: item.index)
            ]
        except RateLimitError as error:
            if attempt == MAX_EMBED_RETRIES - 1:
                raise
            retry_after = error.response.headers.get("retry-after") if error.response else None
            # TPM windows are one minute; a short retry usually receives another 429.
            delay = max(float(retry_after or 0), 65.0 * (attempt + 1))
            delay += random.uniform(0, 5)
            print(f"OpenAI rate limit reached; waiting {delay:.1f}s before retry", flush=True)
            time.sleep(delay)
    raise AssertionError("unreachable")


def _batches(cursor: Any) -> Iterator[list[tuple[Any, ...]]]:
    while rows := cursor.fetchmany(BATCH_SIZE):
        yield rows


def _publish(pending: Path, index_root: Path) -> Path:
    generations = index_root / ".generations"
    generations.mkdir(parents=True, exist_ok=True)
    if generations.is_symlink():
        raise ValueError("RAG .generations must not be a symlink")
    generation = generations / uuid.uuid4().hex
    os.replace(pending, generation)
    pointer = index_root / f".current-{uuid.uuid4().hex}"
    try:
        pointer.symlink_to(Path(".generations") / generation.name, target_is_directory=True)
        os.replace(pointer, index_root / "current")
    finally:
        pointer.unlink(missing_ok=True)
    return generation


def build_index(
    source: Path = DEFAULT_SOURCE,
    index_path: Path = DEFAULT_INDEX,
    collection_name: str = DEFAULT_COLLECTION,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    limit: int | None = None,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    """Index one canonical Silver narrative per complaint ID with bounded in-flight batches."""
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if workers < 1:
        raise ValueError("workers must be positive")
    source = source.resolve(strict=True)
    embedding_client = _openai_embedding_client()
    index_path.mkdir(parents=True, exist_ok=True)
    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=index_path))
    truncated = indexed = eligible = 0
    try:
        connection = duckdb.connect(str(source), read_only=True)
        required = {
            "complaint_id",
            "consumer_complaint_narrative",
            "company",
            "company_key",
            "brand_name",
            "date_received",
        }
        columns = {row[0] for row in connection.execute("DESCRIBE complaints").fetchall()}
        if missing := required - columns:
            raise ValueError(f"Silver lacks columns: {', '.join(sorted(missing))}")
        eligible_query = """
            SELECT count(*) FROM complaints
            WHERE consumer_complaint_narrative IS NOT NULL
              AND length(trim(consumer_complaint_narrative)) > 0
        """
        eligible = connection.execute(eligible_query).fetchone()[0]
        query = """
            SELECT complaint_id, consumer_complaint_narrative, product, sub_product,
                   issue, sub_issue, company, company_key, brand_name, date_received,
                   state, timely_response, company_response_to_consumer
            FROM complaints
            WHERE consumer_complaint_narrative IS NOT NULL
              AND length(trim(consumer_complaint_narrative)) > 0
            ORDER BY complaint_id
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        collection = chromadb.PersistentClient(path=str(pending / "chroma")).create_collection(
            collection_name,
            metadata={"embedding_model": embedding_model, "source_sha256": _sha256(source)},
        )
        futures: deque[Future[tuple[list[tuple[Any, ...]], list[list[float]]]]] = deque()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            cursor = connection.execute(query)
            for batch in _batches(cursor):
                if limit is not None and indexed + len(batch) > limit:
                    batch = batch[: limit - indexed]
                if not batch:
                    break
                futures.append(pool.submit(_embed, embedding_client, batch, embedding_model))
                if len(futures) >= workers:
                    rows, vectors = futures.popleft().result()
                    collection.upsert(
                        ids=[_document_id(str(row[0])) for row in rows],
                        embeddings=vectors,
                        documents=[_document(row) for row in rows],
                        metadatas=[_metadata(row) for row in rows],
                    )
                    indexed += len(rows)
                    truncated += sum(len(row[1]) > MAX_NARRATIVE_CHARS for row in rows)
            while futures:
                rows, vectors = futures.popleft().result()
                collection.upsert(
                    ids=[_document_id(str(row[0])) for row in rows],
                    embeddings=vectors,
                    documents=[_document(row) for row in rows],
                    metadatas=[_metadata(row) for row in rows],
                )
                indexed += len(rows)
                truncated += sum(len(row[1]) > MAX_NARRATIVE_CHARS for row in rows)
        connection.close()
        manifest = {
            "collection": collection_name,
            "metadata_schema_version": 2,
            "embedding_model": embedding_model,
            "embedding_provider": "openai",
            "embedding_base_url": _embedding_config()[1],
            "source": str(source),
            "source_sha256": _sha256(source),
            "built_at_utc": datetime.now(UTC).isoformat(),
            "eligible_silver_narratives": eligible,
            "documents_indexed": indexed,
            "collection_count": collection.count(),
            "limit": limit,
            "max_narrative_chars": MAX_NARRATIVE_CHARS,
            "truncated_narrative_count": truncated,
            "truncation_policy": (
                "Embeddings and returned excerpts contain the first max_narrative_chars only."
            ),
            "reconciles_to_eligible_silver": indexed == eligible
            if limit is None
            else indexed <= eligible,
            "filters": [
                "company",
                "company_key",
                "brand_name",
                "product",
                "sub_product",
                "issue",
                "sub_issue",
                "state",
                "date_received",
                "date_received_ordinal",
                "timely_response",
                "company_response",
            ],
        }
        (pending / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        if collection.count() != indexed:
            raise RuntimeError("Chroma collection count does not reconcile")
        if limit is None and indexed != eligible:
            raise RuntimeError(
                "unlimited RAG build does not reconcile to eligible Silver narratives"
            )
        _publish(pending, index_path)
        return manifest
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def main() -> None:
    load_dotenv(override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--index-path", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    print(
        json.dumps(
            build_index(
                args.source,
                args.index_path,
                args.collection,
                args.embedding_model,
                args.limit,
                args.workers,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
