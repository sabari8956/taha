"""Bounded deterministic synthesis over already-collected evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from meridian_assistant.analytics import StructuredResult
from meridian_assistant.contracts import CoverageResult, EntityResolution
from meridian_assistant.llm import DEFAULT_OPENAI_MODEL, openai_client
from meridian_assistant.retrieval import RetrievalResult

AnswerMode = Literal["structured", "narrative", "combined"]
SynthesisProfile = Literal[
    "structured_summary",
    "ranked_issues",
    "narrative_examples",
    "sample_themes",
    "response_distribution",
    "trend_drivers",
    "quarter_drivers",
]


@dataclass(frozen=True)
class SourceProvenance:
    gold_database: str | None = None
    gold_manifest_sha256: str | None = None
    rag_generation: str | None = None
    rag_manifest_sha256: str | None = None


@dataclass(frozen=True)
class EvidenceBundle:
    question_id: str
    question: str
    mode: AnswerMode
    coverage: CoverageResult
    entity_resolution: EntityResolution | None
    structured: StructuredResult | None
    retrieval: RetrievalResult | None
    provenance: SourceProvenance
    limitations: tuple[str, ...] = ()
    synthesis_profile: SynthesisProfile = "structured_summary"


@dataclass(frozen=True)
class StructuredReference:
    row_index: int
    columns: tuple[str, ...]


@dataclass(frozen=True)
class NarrativeReference:
    complaint_id: str
    quote: str


@dataclass(frozen=True)
class SampleThemeReference:
    issue: str
    sub_issue: str | None
    retrieved_count: int


@dataclass(frozen=True)
class DraftClaim:
    kind: Literal["quantitative", "narrative", "sample_theme", "limitation"]
    template: str
    structured_refs: tuple[StructuredReference, ...] = ()
    narrative_refs: tuple[NarrativeReference, ...] = ()
    sample_theme_refs: tuple[SampleThemeReference, ...] = ()


@dataclass(frozen=True)
class SynthesisDraft:
    claims: tuple[DraftClaim, ...]
    interpretation_choices: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class SynthesisAdapter(Protocol):
    def synthesize(self, question: str, evidence: EvidenceBundle) -> SynthesisDraft: ...


def evidence_bundle_sha256(evidence: EvidenceBundle) -> str:
    """Hash the bounded typed evidence deterministically for audit correlation."""
    payload = json.dumps(asdict(evidence), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class OpenAISynthesisAdapter:
    """LLM evidence narrator that emits only validator-controlled draft claims.

    The model sees the bounded evidence bundle, but cannot return free-form final
    prose. It chooses grounded claims and exact evidence references; the existing
    validator and renderer remain the authority for citations and final wording.
    """

    identity = "openai-grounded-narrator-v1"

    def __init__(self, *, model: str | None = None, client: Any | None = None) -> None:
        self.model = model or DEFAULT_OPENAI_MODEL
        self.client = client

    def synthesize(self, question: str, evidence: EvidenceBundle) -> SynthesisDraft:
        payload = json.dumps(asdict(evidence), default=str, separators=(",", ":"))
        system = (
            "Select grounded answer claims from the supplied evidence. Return JSON only with "
            "claims, interpretation_choices, and limitations. A claim has kind, template, "
            "structured_refs, narrative_refs, and sample_theme_refs. Never add facts, numbers, "
            "Complaint IDs, quotes, or limitations not present in the evidence. Quantitative "
            "claims must cite exactly one existing row and its complete columns. Narrative claims "
            "must quote an exact contiguous excerpt and cite its existing Complaint ID. Use only "
            "the established templates: structured_row, ranked_issue_row, response_category_row, "
            "comparison_total, issue_driver, quarter_comparison_total, quarter_issue_driver, "
            "attributed_complaint, sample_complaint, retrieved_sample_themes."
        )
        client = self.client or openai_client()
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps({"question": question, "evidence": payload}),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content
        decoded = json.loads(content) if content else None
        if not isinstance(decoded, dict) or not isinstance(decoded.get("claims"), list):
            raise ValueError("narrator returned malformed JSON")
        claims: list[DraftClaim] = []
        for raw in decoded["claims"]:
            if not isinstance(raw, dict):
                raise ValueError("narrator claim is invalid")
            structured = tuple(
                StructuredReference(int(ref["row_index"]), tuple(ref["columns"]))
                for ref in raw.get("structured_refs", [])
            )
            narratives = tuple(
                NarrativeReference(str(ref["complaint_id"]), str(ref["quote"]))
                for ref in raw.get("narrative_refs", [])
            )
            themes = tuple(
                SampleThemeReference(
                    str(ref["issue"]), ref.get("sub_issue"), int(ref["retrieved_count"])
                )
                for ref in raw.get("sample_theme_refs", [])
            )
            claims.append(
                DraftClaim(str(raw["kind"]), str(raw["template"]), structured, narratives, themes)
            )  # type: ignore[arg-type]
        choices = decoded.get("interpretation_choices", [])
        limitations = decoded.get("limitations", [])
        if not all(isinstance(value, str) for value in [*choices, *limitations]):
            raise ValueError("narrator text fields must be strings")
        return SynthesisDraft(tuple(claims), tuple(choices), tuple(limitations))


class TemplateSynthesisAdapter:
    """Select only fixed templates and exact evidence references; performs no I/O."""

    identity = "template-synthesis-v2"

    @staticmethod
    def _bounded_quote(excerpt: str) -> str:
        normalized = " ".join(excerpt.split())
        if len(normalized) <= 280:
            return normalized
        boundary = normalized.rfind(".", 0, 281)
        return normalized[: boundary + 1 if boundary >= 40 else 280].rstrip()

    @staticmethod
    def _sample_themes(evidence: EvidenceBundle) -> tuple[SampleThemeReference, ...]:
        if evidence.retrieval is None:
            return ()
        counts: dict[tuple[str, str | None], int] = {}
        for item in evidence.retrieval.evidence:
            issue = " ".join(item.metadata.get("issue", "").split())
            sub_issue = " ".join(item.metadata.get("sub_issue", "").split()) or None
            if not issue:
                continue
            key = (issue, sub_issue)
            counts[key] = counts.get(key, 0) + 1
        repeated = [
            SampleThemeReference(issue, sub_issue, count)
            for (issue, sub_issue), count in counts.items()
            if count >= 2
        ]
        return tuple(
            sorted(
                repeated,
                key=lambda theme: (
                    -theme.retrieved_count,
                    theme.issue.casefold(),
                    (theme.sub_issue or "").casefold(),
                ),
            )[:3]
        )

    @staticmethod
    def _examples(evidence: EvidenceBundle) -> list[tuple[str, str]]:
        if evidence.retrieval is None:
            return []
        selected: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        seen_quotes: set[str] = set()
        for item in evidence.retrieval.evidence:
            quote = TemplateSynthesisAdapter._bounded_quote(item.excerpt)
            fingerprint = "".join(quote.casefold().split())
            if not quote or item.complaint_id in seen_ids or fingerprint in seen_quotes:
                continue
            selected.append((item.complaint_id, quote))
            seen_ids.add(item.complaint_id)
            seen_quotes.add(fingerprint)
            if len(selected) == 3:
                break
        return selected

    def synthesize(self, question: str, evidence: EvidenceBundle) -> SynthesisDraft:
        claims: list[DraftClaim] = []
        interpretations: list[str] = []
        limitations = list(evidence.limitations)
        rows = evidence.structured.rows if evidence.structured else []
        profile = evidence.synthesis_profile
        if rows:
            if profile == "ranked_issues":
                for index, row in enumerate(rows[:5]):
                    claims.append(
                        DraftClaim(
                            "quantitative",
                            "ranked_issue_row",
                            (StructuredReference(index, tuple(row.keys())),),
                        )
                    )
            elif profile == "response_distribution":
                for index, row in enumerate(rows):
                    claims.append(
                        DraftClaim(
                            "quantitative",
                            "response_category_row",
                            (StructuredReference(index, tuple(row.keys())),),
                        )
                    )
                interpretations.append(
                    "Closed with monetary relief and Closed with non-monetary relief are CFPB "
                    "relief categories; Closed with explanation is only a no-relief proxy, not "
                    "proof of the consumer's actual outcome or dollar recovery."
                )
            elif profile in {"trend_drivers", "quarter_drivers"}:
                total_template = (
                    "quarter_comparison_total"
                    if profile == "quarter_drivers"
                    else "comparison_total"
                )
                driver_template = (
                    "quarter_issue_driver" if profile == "quarter_drivers" else "issue_driver"
                )
                claims.append(
                    DraftClaim(
                        "quantitative",
                        total_template,
                        (StructuredReference(0, tuple(rows[0].keys())),),
                    )
                )
                for index, row in enumerate(rows[1:4], start=1):
                    claims.append(
                        DraftClaim(
                            "quantitative",
                            driver_template,
                            (StructuredReference(index, tuple(row.keys())),),
                        )
                    )
            else:
                row = rows[0]
                claims.append(
                    DraftClaim(
                        "quantitative",
                        "structured_row",
                        (StructuredReference(0, tuple(row.keys())),),
                    )
                )
                if "timely_response_known_count" in row:
                    interpretations.append(
                        "Timely-response rates exclude records whose timely-response value "
                        "is unknown."
                    )
                    interpretations.append(
                        "The winner is the lowest timely-response rate within the ten "
                        "highest-volume companies; ties are resolved by company name."
                    )
        if profile == "sample_themes":
            themes = self._sample_themes(evidence)
            if themes:
                claims.append(
                    DraftClaim(
                        "sample_theme",
                        "retrieved_sample_themes",
                        sample_theme_refs=themes,
                    )
                )
            else:
                limitations.append(
                    "No issue/sub-issue label repeated among the retrieved examples; only "
                    "individual examples are shown."
                )
        for complaint_id, quote in self._examples(evidence):
            claims.append(
                DraftClaim(
                    "narrative",
                    "sample_complaint" if profile == "sample_themes" else "attributed_complaint",
                    narrative_refs=(NarrativeReference(complaint_id, quote),),
                )
            )
        if profile in {"sample_themes", "narrative_examples"}:
            limitations.append(
                "These are relevance-ranked retrieved examples, not population prevalence "
                "estimates."
            )
        if evidence.retrieval and any(item.truncated for item in evidence.retrieval.evidence):
            limitations.append(
                "At least one cited narrative was truncated by the index excerpt limit."
            )
        return SynthesisDraft(
            tuple(claims), tuple(dict.fromkeys(interpretations)), tuple(dict.fromkeys(limitations))
        )
