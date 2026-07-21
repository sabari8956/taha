from __future__ import annotations

from dataclasses import dataclass, replace

from meridian_assistant.analytics import AnalysisFilters, AnalysisPlan, StructuredResult
from meridian_assistant.assistant import (
    AssistantDependencies,
    AssistantPlan,
    NarrativeBinding,
    execute_plan,
)
from meridian_assistant.contracts import CoverageResult, DataCapabilities, EntityResolution
from meridian_assistant.retrieval import (
    NarrativeEvidence,
    NarrativeFilters,
    RetrievalResult,
    RetrievalStatus,
)


@dataclass
class Fakes:
    structured_calls: int = 0
    retrieval_calls: int = 0
    received_analysis: AnalysisPlan | None = None
    received_filters: NarrativeFilters | None = None

    def dependencies(self, retrieval: RetrievalResult) -> AssistantDependencies:
        capabilities = DataCapabilities("2024-01-01", "2025-12-31", True)

        def coverage(capabilities: DataCapabilities, **kwargs: object) -> CoverageResult:
            if kwargs["requires_verified_relief_amount"]:
                return CoverageResult(
                    False, "No verified relief amount field.", "2024-01-01", "2025-12-31"
                )
            return CoverageResult(True, None, "2024-01-01", "2025-12-31")

        def resolve(value: str) -> EntityResolution:
            return EntityResolution(value, "Block, Inc.", "Cash App", "curated_alias")

        def structured(plan: AnalysisPlan) -> StructuredResult:
            self.structured_calls += 1
            self.received_analysis = plan
            return StructuredResult(
                [{"company": "Block, Inc.", "complaint_count": 7}],
                "SELECT company, 7 AS complaint_count",
                (),
                "gold/current/metrics.duckdb",
            )

        def retrieve(_: str, filters: NarrativeFilters, ___: int) -> RetrievalResult:
            self.retrieval_calls += 1
            self.received_filters = filters
            metadata = {
                key: str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in {
                    "company": filters.company,
                    "brand_name": filters.brand_name,
                    "product": filters.product,
                    "sub_product": filters.sub_product,
                    "issue": filters.issue,
                    "sub_issue": filters.sub_issue,
                    "state": filters.state,
                    "timely_response": filters.timely_response,
                    "company_response": filters.company_response,
                    "date_received": filters.start.isoformat() if filters.start else None,
                }.items()
                if value is not None
            }
            return replace(
                retrieval,
                filters=filters,
                evidence=tuple(
                    replace(item, metadata=item.metadata or metadata) for item in retrieval.evidence
                ),
            )

        return AssistantDependencies(capabilities, coverage, resolve, structured, retrieve)


def _analysis() -> AnalysisPlan:
    return AnalysisPlan("aggregate", dimensions=("company",))


def _evidence(identifier: str = "12345") -> NarrativeEvidence:
    return NarrativeEvidence(identifier, "A cited complaint excerpt.", {}, 0.1, False)


def test_structured_only_attaches_executed_query_and_trace() -> None:
    fakes = Fakes()
    result = execute_plan(
        AssistantPlan(
            "q03", "Block had the lowest measured rate.", "structured", analysis=_analysis()
        ),
        fakes.dependencies(RetrievalResult(RetrievalStatus.EMPTY, "", NarrativeFilters())),
    )
    assert result.answer.query == "SELECT company, 7 AS complaint_count"
    assert result.answer.citations == ()
    assert not result.answer.abstained
    assert result.trace.steps == (
        "coverage_check",
        "run_structured_analysis",
        "synthesize",
        "validate_draft",
    )
    assert result.trace.structured_row_count == 1
    assert fakes.structured_calls == 1 and fakes.retrieval_calls == 0


def test_resolved_company_constrains_both_evidence_tools() -> None:
    fakes = Fakes()
    retrieval = RetrievalResult(RetrievalStatus.OK, "cash app", NarrativeFilters(), (_evidence(),))
    result = execute_plan(
        AssistantPlan(
            "q11",
            "Cash App evidence is constrained to Block.",
            "combined",
            analysis=_analysis(),
            narrative_query="Cash App problems",
            company_or_brand="Cash App",
        ),
        fakes.dependencies(retrieval),
    )
    assert not result.answer.abstained
    assert fakes.received_analysis is not None
    assert fakes.received_analysis.filters.company == "Block, Inc."
    assert fakes.received_filters is not None
    assert fakes.received_filters.company is None
    assert fakes.received_filters.brand_name == "Cash App"


def test_conflicting_explicit_company_filter_is_rejected() -> None:
    fakes = Fakes()
    analysis = AnalysisPlan(
        "aggregate", dimensions=("company",), filters=AnalysisFilters(company="Other Bank")
    )
    try:
        execute_plan(
            AssistantPlan(
                "q02",
                "Conflicting company.",
                "structured",
                analysis=analysis,
                company_or_brand="Cash App",
            ),
            fakes.dependencies(RetrievalResult(RetrievalStatus.EMPTY, "", NarrativeFilters())),
        )
    except ValueError as error:
        assert "conflicts" in str(error)
    else:
        raise AssertionError("conflicting company constraints must be rejected")


def test_narrative_only_uses_numeric_citations_and_entity_trace() -> None:
    fakes = Fakes()
    retrieval = RetrievalResult(RetrievalStatus.OK, "freeze", NarrativeFilters(), (_evidence(),))
    result = execute_plan(
        AssistantPlan(
            "q12",
            "Consumers describe serious disruption.",
            "narrative",
            narrative_query="account freeze",
            company_or_brand="Cash App",
        ),
        fakes.dependencies(retrieval),
    )
    assert result.answer.citations == (12345,)
    assert result.answer.query is None
    assert result.trace.resolved_company == "Block, Inc."
    assert result.trace.steps == (
        "coverage_check",
        "resolve_company",
        "retrieve_narratives",
        "synthesize",
        "validate_draft",
    )
    assert fakes.structured_calls == 0 and fakes.retrieval_calls == 1


def test_combined_requires_both_evidence_kinds() -> None:
    fakes = Fakes()
    retrieval = RetrievalResult(RetrievalStatus.OK, "spike", NarrativeFilters(), (_evidence(),))
    result = execute_plan(
        AssistantPlan(
            "q07",
            "Block had a spike and narratives describe the cause.",
            "combined",
            analysis=_analysis(),
            narrative_query="why complaints rose",
        ),
        fakes.dependencies(retrieval),
    )
    assert not result.answer.abstained
    assert result.answer.query is not None
    assert result.answer.citations == (12345,)
    assert result.trace.steps == (
        "coverage_check",
        "run_structured_analysis",
        "retrieve_narratives",
        "synthesize",
        "validate_draft",
    )


def test_capability_abstention_occurs_before_other_tools() -> None:
    fakes = Fakes()
    result = execute_plan(
        AssistantPlan(
            "q15",
            "No answer.",
            "combined",
            analysis=_analysis(),
            narrative_query="relief",
            requires_verified_relief_amount=True,
        ),
        fakes.dependencies(
            RetrievalResult(RetrievalStatus.OK, "", NarrativeFilters(), (_evidence(),))
        ),
    )
    assert result.answer.abstained
    assert result.answer.citations == () and result.answer.query is None
    assert result.trace.steps == ("coverage_check",)
    assert fakes.structured_calls == 0 and fakes.retrieval_calls == 0


def test_missing_narrative_evidence_abstains_with_structured_query() -> None:
    fakes = Fakes()
    result = execute_plan(
        AssistantPlan(
            "q07", "A mixed answer.", "combined", analysis=_analysis(), narrative_query="cause"
        ),
        fakes.dependencies(RetrievalResult(RetrievalStatus.EMPTY, "cause", NarrativeFilters())),
    )
    assert result.answer.abstained
    assert result.answer.query == "SELECT company, 7 AS complaint_count"
    assert result.answer.citations == ()
    assert result.trace.retrieval_status is RetrievalStatus.EMPTY


def test_retrieval_error_abstains_with_trace_invariants() -> None:
    fakes = Fakes()
    result = execute_plan(
        AssistantPlan("q01", "Narrative answer.", "narrative", narrative_query="topic"),
        fakes.dependencies(
            RetrievalResult(
                RetrievalStatus.INDEX_ERROR, "topic", NarrativeFilters(), error="index unavailable"
            )
        ),
    )
    assert result.answer.abstained
    assert result.answer.citations == () and result.answer.query is None
    assert result.trace.retrieval_status is RetrievalStatus.INDEX_ERROR
    assert result.trace.retrieved_evidence_count == result.trace.accepted_citation_count == 0


def test_mixed_or_duplicate_citations_fail_closed() -> None:
    fakes = Fakes()
    retrieval = RetrievalResult(
        RetrievalStatus.OK,
        "topic",
        NarrativeFilters(),
        (_evidence("12345"), _evidence("00123"), _evidence("12345"), _evidence("67890")),
    )
    result = execute_plan(
        AssistantPlan("q01", "A narrative answer.", "narrative", narrative_query="topic"),
        fakes.dependencies(retrieval),
    )
    assert result.answer.abstained
    assert result.answer.citations == ()
    assert result.trace.retrieved_evidence_count == 4
    assert result.trace.accepted_citation_count == 0
    assert any("00123" in limitation for limitation in result.trace.limitations)


def test_empty_structured_result_abstains_with_query_and_trace_invariants() -> None:
    fakes = Fakes()
    dependencies = fakes.dependencies(
        RetrievalResult(RetrievalStatus.EMPTY, "", NarrativeFilters())
    )

    def empty_structured(_: AnalysisPlan) -> StructuredResult:
        fakes.structured_calls += 1
        return StructuredResult([], "SELECT nothing", (), "gold/current/metrics.duckdb")

    result = execute_plan(
        AssistantPlan("q03", "No rows.", "structured", analysis=_analysis()),
        replace(dependencies, analytics_runner=empty_structured),
    )
    assert result.answer.abstained
    assert result.answer.query == "SELECT nothing" and result.answer.citations == ()
    assert result.trace.structured_row_count == result.trace.accepted_citation_count == 0
    assert result.trace.retrieval_status is None and fakes.retrieval_calls == 0


def test_non_lossless_citation_is_not_emitted() -> None:
    fakes = Fakes()
    retrieval = RetrievalResult(
        RetrievalStatus.OK, "topic", NarrativeFilters(), (_evidence("00123"),)
    )
    result = execute_plan(
        AssistantPlan("q01", "A narrative answer.", "narrative", narrative_query="topic"),
        fakes.dependencies(retrieval),
    )
    assert result.answer.abstained
    assert result.answer.citations == ()
    assert "not canonical decimal" in result.answer.answer


def test_unresolved_explicit_entity_fails_closed_before_data_tools() -> None:
    fakes = Fakes()
    dependencies = fakes.dependencies(
        RetrievalResult(RetrievalStatus.OK, "topic", NarrativeFilters(), (_evidence(),))
    )
    dependencies = replace(
        dependencies,
        company_resolver=lambda value: EntityResolution(value, None, None, "unresolved"),
    )
    result = execute_plan(
        AssistantPlan(
            "q99",
            mode="combined",
            analysis=_analysis(),
            narrative_query="unknown company",
            company_or_brand="Unknown Co",
        ),
        dependencies,
    )
    assert result.answer.abstained
    assert result.trace.steps == ("coverage_check", "resolve_company")
    assert fakes.structured_calls == fakes.retrieval_calls == 0


def test_q03_binds_retrieval_to_winner_and_late_cases() -> None:
    fakes = Fakes()
    result = execute_plan(
        AssistantPlan(
            "q03",
            mode="combined",
            analysis=AnalysisPlan("top_volume_timely_response"),
            narrative_query="late cases",
            narrative_filters=NarrativeFilters(timely_response=False),
            narrative_binding=NarrativeBinding(0, "company"),
        ),
        fakes.dependencies(
            RetrievalResult(RetrievalStatus.OK, "late", NarrativeFilters(), (_evidence(),))
        ),
    )
    assert not result.answer.abstained
    assert fakes.received_filters == NarrativeFilters(company="Block, Inc.", timely_response=False)


def test_retrieval_uses_expanded_candidates_then_preserves_issue_diversity() -> None:
    fakes = Fakes()
    retrieval = RetrievalResult(
        RetrievalStatus.OK,
        "topic",
        NarrativeFilters(),
        (
            NarrativeEvidence("1", "first", {"issue": "A", "sub_issue": "a"}, 0.1, False),
            NarrativeEvidence("2", "second", {"issue": "A", "sub_issue": "a"}, 0.2, False),
            NarrativeEvidence("3", "third", {"issue": "B", "sub_issue": "b"}, 0.3, False),
            NarrativeEvidence("4", "fourth", {"issue": "C", "sub_issue": "c"}, 0.4, False),
        ),
    )
    received_top_k: list[int] = []
    dependencies = fakes.dependencies(retrieval)
    original = dependencies.retrieval_runner

    def diverse(query: str, filters: NarrativeFilters, top_k: int) -> RetrievalResult:
        received_top_k.append(top_k)
        return original(query, filters, top_k)

    result = execute_plan(
        AssistantPlan(
            "q06",
            mode="narrative",
            narrative_query="examples",
            top_k=2,
            synthesis_profile="sample_themes",
        ),
        replace(dependencies, retrieval_runner=diverse),
    )
    assert received_top_k == [6]
    assert result.evidence and result.evidence.retrieval
    assert [item.complaint_id for item in result.evidence.retrieval.evidence] == ["1", "3"]
    assert "not population prevalence" in result.answer.answer


def test_synthesis_adapter_failure_abstains_and_preserves_executed_sql() -> None:
    class BrokenAdapter:
        identity = "broken"

        def synthesize(self, question: str, evidence: object) -> object:
            raise ValueError("invalid adapter payload")

    fakes = Fakes()
    dependencies = replace(
        fakes.dependencies(RetrievalResult(RetrievalStatus.EMPTY, "", NarrativeFilters())),
        synthesis_adapter=BrokenAdapter(),  # type: ignore[arg-type]
    )
    result = execute_plan(AssistantPlan("q", mode="structured", analysis=_analysis()), dependencies)
    assert result.answer.abstained
    assert result.answer.query == "SELECT company, 7 AS complaint_count"
    assert result.answer.citations == ()
    assert result.trace.steps[-1] == "synthesis_failure"


def test_analytics_operational_failure_is_not_hidden_as_synthesis_abstention() -> None:
    fakes = Fakes()
    dependencies = replace(
        fakes.dependencies(RetrievalResult(RetrievalStatus.EMPTY, "", NarrativeFilters())),
        analytics_runner=lambda plan: (_ for _ in ()).throw(RuntimeError("duckdb failed")),
    )
    try:
        execute_plan(AssistantPlan("q", mode="structured", analysis=_analysis()), dependencies)
    except RuntimeError as error:
        assert str(error) == "duckdb failed"
    else:
        raise AssertionError("analytics operational failures must remain operational")


def test_plan_validates_required_tool_intents() -> None:
    try:
        AssistantPlan("q01", "Answer", "narrative")
    except ValueError as error:
        assert "narrative_query" in str(error)
    else:
        raise AssertionError("narrative plans must require a query")

    try:
        AssistantPlan("q03", "Answer", "structured", analysis=None)
    except ValueError as error:
        assert "require analysis" in str(error)
    else:
        raise AssertionError("structured plans must require analysis")
