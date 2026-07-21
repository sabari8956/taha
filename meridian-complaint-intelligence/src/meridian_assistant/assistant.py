"""Deterministic coverage, evidence collection, synthesis, and validation coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable

from meridian_assistant.analytics import AnalysisPlan, StructuredResult, run_analysis
from meridian_assistant.contracts import (
    AnswerRecord,
    CoverageResult,
    DataCapabilities,
    EntityResolution,
)
from meridian_assistant.coverage import check_coverage, resolve_company
from meridian_assistant.retrieval import (
    NarrativeFilters,
    RetrievalResult,
    RetrievalStatus,
    retrieve_narratives,
)
from meridian_assistant.synthesis import (
    AnswerMode,
    EvidenceBundle,
    SourceProvenance,
    SynthesisAdapter,
    SynthesisProfile,
    TemplateSynthesisAdapter,
    evidence_bundle_sha256,
)
from meridian_assistant.validation import ValidationResult, render_validated, validate_draft


@dataclass(frozen=True)
class NarrativeBinding:
    company_from_row: int | None = None
    company_column: str | None = None


@dataclass(frozen=True)
class AssistantPlan:
    question_id: str
    answer_text: str = ""  # compatibility only; never used to render factual output
    mode: AnswerMode = "narrative"
    analysis: AnalysisPlan | None = None
    narrative_query: str | None = None
    narrative_filters: NarrativeFilters = field(default_factory=NarrativeFilters)
    narrative_binding: NarrativeBinding | None = None
    company_or_brand: str | None = None
    requested_start: date | None = None
    requested_end: date | None = None
    requires_verified_relief_amount: bool = False
    requires_auto_insurance_claims: bool = False
    top_k: int = 8
    synthesis_profile: SynthesisProfile = "structured_summary"

    def __post_init__(self) -> None:
        if not self.question_id.strip():
            raise ValueError("question_id must be nonblank")
        if self.mode in {"structured", "combined"} and self.analysis is None:
            raise ValueError("structured and combined plans require analysis")
        if self.mode in {"narrative", "combined"} and not (self.narrative_query or "").strip():
            raise ValueError("narrative and combined plans require narrative_query")
        if self.top_k < 1:
            raise ValueError("top_k must be positive")


@dataclass(frozen=True)
class ToolTrace:
    steps: tuple[str, ...]
    coverage: CoverageResult
    resolved_company: str | None
    structured_query: str | None
    structured_row_count: int
    retrieval_status: RetrievalStatus | None
    retrieved_evidence_count: int
    accepted_citation_count: int
    limitations: tuple[str, ...]
    entity_resolution: EntityResolution | None = None
    structured_parameters: tuple[object, ...] = ()
    executed_narrative_filters: NarrativeFilters | None = None
    synthesis_adapter: str | None = None
    validation: ValidationResult | None = None
    evidence_sha256: str | None = None


@dataclass(frozen=True)
class AssistantResponse:
    answer: AnswerRecord
    trace: ToolTrace
    evidence: EvidenceBundle | None = None


@dataclass(frozen=True)
class AssistantDependencies:
    capabilities: DataCapabilities
    coverage_check: Callable[..., CoverageResult] = check_coverage
    company_resolver: Callable[[str], EntityResolution] = resolve_company
    analytics_runner: Callable[[AnalysisPlan], StructuredResult] = run_analysis
    retrieval_runner: Callable[..., RetrievalResult] = retrieve_narratives
    synthesis_adapter: SynthesisAdapter = field(default_factory=TemplateSynthesisAdapter)
    gold_manifest_sha256: str | None = None


def _same_company(left: str, right: str) -> bool:
    def normalize(value: str) -> str:
        return "".join(c for c in value.casefold() if c.isalnum())

    return normalize(left) == normalize(right)


def _apply_entity(
    plan: AssistantPlan, resolution: EntityResolution | None
) -> tuple[AnalysisPlan | None, NarrativeFilters]:
    analysis, narrative = plan.analysis, plan.narrative_filters
    if resolution is None:
        return analysis, narrative
    assert resolution.legal_company is not None
    if analysis is not None:
        explicit = analysis.filters.company
        if explicit and not _same_company(explicit, resolution.legal_company):
            raise ValueError("analysis company filter conflicts with resolved company")
        analysis = replace(
            analysis, filters=replace(analysis.filters, company=resolution.legal_company)
        )
    if narrative.company and not _same_company(narrative.company, resolution.legal_company):
        raise ValueError("narrative legal-company filter conflicts with resolved company")
    if narrative.brand_name and narrative.brand_name != resolution.brand_name:
        raise ValueError("narrative brand filter conflicts with resolved brand")
    narrative = replace(
        narrative,
        company=resolution.legal_company if resolution.brand_name is None else narrative.company,
        brand_name=resolution.brand_name,
    )
    return analysis, narrative


def _diverse_evidence(retrieval: RetrievalResult, target: int) -> RetrievalResult:
    """Keep bounded, deterministic coverage from an expanded relevance-ranked candidate set."""
    if len(retrieval.evidence) <= target:
        return retrieval
    selected = []
    seen_labels: set[tuple[str, str]] = set()
    for item in retrieval.evidence:
        label = (item.metadata.get("issue", ""), item.metadata.get("sub_issue", ""))
        if label not in seen_labels:
            selected.append(item)
            seen_labels.add(label)
        if len(selected) == target:
            return replace(retrieval, evidence=tuple(selected))
    for item in retrieval.evidence:
        if item not in selected:
            selected.append(item)
        if len(selected) == target:
            break
    return replace(retrieval, evidence=tuple(selected))


def _trace(
    steps: list[str],
    coverage: CoverageResult,
    *,
    resolution: EntityResolution | None = None,
    structured: StructuredResult | None = None,
    retrieval: RetrievalResult | None = None,
    limitations: tuple[str, ...] = (),
    adapter: str | None = None,
    validation: ValidationResult | None = None,
) -> ToolTrace:
    return ToolTrace(
        tuple(steps),
        coverage,
        resolution.legal_company if resolution else None,
        structured.executed_sql if structured else None,
        len(structured.rows) if structured else 0,
        retrieval.status if retrieval else None,
        len(retrieval.evidence) if retrieval else 0,
        len(validation.accepted_citation_ids) if validation and validation.accepted else 0,
        limitations,
        resolution,
        structured.parameters if structured else (),
        retrieval.filters if retrieval else None,
        adapter,
        validation,
        None,
    )


def collect_evidence(
    plan: AssistantPlan, dependencies: AssistantDependencies, question: str = ""
) -> tuple[EvidenceBundle | None, ToolTrace, str | None]:
    steps = ["coverage_check"]
    coverage = dependencies.coverage_check(
        dependencies.capabilities,
        requested_start=plan.requested_start,
        requested_end=plan.requested_end,
        requires_verified_relief_amount=plan.requires_verified_relief_amount,
        requires_auto_insurance_claims=plan.requires_auto_insurance_claims,
    )
    if not coverage.answerable:
        reason = coverage.reason or "The available data cannot support this request."
        return None, _trace(steps, coverage, limitations=(reason,)), reason

    resolution: EntityResolution | None = None
    if plan.company_or_brand is not None:
        steps.append("resolve_company")
        resolution = dependencies.company_resolver(plan.company_or_brand)
        if resolution.status == "unresolved" or resolution.legal_company is None:
            reason = (
                f"The explicit company or brand {plan.company_or_brand!r} could not be resolved."
            )
            return (
                None,
                _trace(steps, coverage, resolution=resolution, limitations=(reason,)),
                reason,
            )
    analysis, narrative_filters = _apply_entity(plan, resolution)

    structured: StructuredResult | None = None
    if analysis is not None:
        steps.append("run_structured_analysis")
        structured = dependencies.analytics_runner(analysis)
        if not structured.rows:
            reason = "The structured query returned no rows for the requested constraints."
            bundle = EvidenceBundle(
                plan.question_id,
                question,
                plan.mode,
                coverage,
                resolution,
                structured,
                None,
                SourceProvenance(
                    structured.source_database,
                    dependencies.gold_manifest_sha256,
                ),
                (reason,),
                plan.synthesis_profile,
            )
            trace = _trace(
                steps,
                coverage,
                resolution=resolution,
                structured=structured,
                limitations=(reason,),
            )
            return bundle, replace(trace, evidence_sha256=evidence_bundle_sha256(bundle)), reason

    if plan.narrative_binding is not None:
        binding = plan.narrative_binding
        if structured is None or binding.company_from_row is None or not binding.company_column:
            raise ValueError("narrative binding requires structured row and company column")
        try:
            company = structured.rows[binding.company_from_row][binding.company_column]
        except (IndexError, KeyError) as error:
            raise ValueError("narrative binding references missing structured evidence") from error
        if not isinstance(company, str) or not company.strip():
            raise ValueError("narrative binding company must be nonblank")
        if narrative_filters.company and not _same_company(narrative_filters.company, company):
            raise ValueError("bound narrative company conflicts with explicit company")
        narrative_filters = replace(narrative_filters, company=company)

    retrieval: RetrievalResult | None = None
    limitations: list[str] = []
    if plan.narrative_query:
        steps.append("retrieve_narratives")
        retrieval = dependencies.retrieval_runner(
            plan.narrative_query, narrative_filters, plan.top_k * 3
        )
        retrieval = _diverse_evidence(retrieval, plan.top_k)
        if retrieval.status is RetrievalStatus.EMPTY:
            limitations.append("No narratives matched the requested retrieval constraints.")
        elif retrieval.status is not RetrievalStatus.OK:
            limitations.append(retrieval.error or "Narrative retrieval was unavailable.")
        if plan.mode in {"narrative", "combined"} and not retrieval.evidence:
            reason = "; ".join(limitations) or "No citable narrative evidence was returned."
            bundle = EvidenceBundle(
                plan.question_id,
                question,
                plan.mode,
                coverage,
                resolution,
                structured,
                retrieval,
                SourceProvenance(
                    structured.source_database if structured else None,
                    dependencies.gold_manifest_sha256,
                    retrieval.generation,
                    retrieval.manifest_sha256,
                ),
                tuple(limitations),
                plan.synthesis_profile,
            )
            trace = _trace(
                steps,
                coverage,
                resolution=resolution,
                structured=structured,
                retrieval=retrieval,
                limitations=tuple(limitations),
            )
            return bundle, replace(trace, evidence_sha256=evidence_bundle_sha256(bundle)), reason

    bundle = EvidenceBundle(
        plan.question_id,
        question,
        plan.mode,
        coverage,
        resolution,
        structured,
        retrieval,
        SourceProvenance(
            structured.source_database if structured else None,
            dependencies.gold_manifest_sha256,
            retrieval.generation if retrieval else None,
            retrieval.manifest_sha256 if retrieval else None,
        ),
        tuple(limitations),
        plan.synthesis_profile,
    )
    trace = _trace(
        steps,
        coverage,
        resolution=resolution,
        structured=structured,
        retrieval=retrieval,
        limitations=tuple(limitations),
    )
    return bundle, replace(trace, evidence_sha256=evidence_bundle_sha256(bundle)), None


def execute_plan(
    plan: AssistantPlan, dependencies: AssistantDependencies, question: str = ""
) -> AssistantResponse:
    evidence, trace, refusal = collect_evidence(plan, dependencies, question)
    if refusal is not None:
        query = evidence.structured.executed_sql if evidence and evidence.structured else None
        return AssistantResponse(
            AnswerRecord(plan.question_id, refusal, (), query, True), trace, evidence
        )
    assert evidence is not None
    adapter = dependencies.synthesis_adapter
    query = evidence.structured.executed_sql if evidence.structured else None
    evidence_hash = evidence_bundle_sha256(evidence)
    try:
        draft = adapter.synthesize(question, evidence)
        validation = validate_draft(evidence, draft)
        rendered = render_validated(evidence, draft) if validation.accepted else None
    except Exception as error:
        reason = f"Synthesis adapter failure: {error}"
        failure_trace = replace(
            trace,
            steps=tuple((*trace.steps, "synthesize", "synthesis_failure")),
            synthesis_adapter=getattr(adapter, "identity", type(adapter).__name__),
            limitations=tuple((*trace.limitations, reason)),
            evidence_sha256=evidence_hash,
        )
        return AssistantResponse(
            AnswerRecord(plan.question_id, reason, (), query, True), failure_trace, evidence
        )
    final_trace = replace(
        trace,
        steps=tuple((*trace.steps, "synthesize", "validate_draft")),
        synthesis_adapter=getattr(adapter, "identity", type(adapter).__name__),
        validation=validation,
        accepted_citation_count=len(validation.accepted_citation_ids),
        limitations=tuple((*trace.limitations, *validation.reasons)),
        evidence_sha256=evidence_hash,
    )
    if not validation.accepted:
        reason = "The synthesized answer failed deterministic grounding validation: " + "; ".join(
            validation.reasons
        )
        return AssistantResponse(
            AnswerRecord(plan.question_id, reason, (), query, True), final_trace, evidence
        )
    citations = tuple(int(value) for value in validation.accepted_citation_ids)
    assert rendered is not None
    return AssistantResponse(
        AnswerRecord(plan.question_id, rendered, citations, query, False), final_trace, evidence
    )
