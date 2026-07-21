"""Typed public contracts for grounded assistant tools and answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AnswerRecord:
    """The serializable answers.json record produced by the assistant."""

    question_id: str
    answer: str
    citations: tuple[int, ...]
    query: str | None
    abstained: bool

    def __post_init__(self) -> None:
        if not self.question_id.strip():
            raise ValueError("question_id must be nonblank")
        if not self.answer.strip():
            raise ValueError("answer must be nonblank")
        if any(
            not isinstance(citation, int) or isinstance(citation, bool)
            for citation in self.citations
        ):
            raise ValueError("answers.json citations must be numeric Complaint ID integers")
        if self.abstained and self.citations:
            raise ValueError("abstained answers must not contain citations")

    def to_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "answer": self.answer,
            "citations": list(self.citations),
            "query": self.query,
            "abstained": self.abstained,
        }


@dataclass(frozen=True)
class EntityResolution:
    input_value: str
    legal_company: str | None
    brand_name: str | None
    status: Literal["curated_alias", "normalized_legal_name", "unresolved"]


@dataclass(frozen=True)
class CoverageResult:
    answerable: bool
    reason: str | None
    snapshot_start: str | None
    snapshot_end: str | None
    required_capability: str | None = None


@dataclass(frozen=True)
class DataCapabilities:
    """Explicit source/schema limits that cannot be repaired by an LLM."""

    snapshot_start: str | None
    snapshot_end: str | None
    has_consumer_narratives: bool
    has_verified_relief_amount: bool = False
    supports_auto_insurance_claims: bool = False
