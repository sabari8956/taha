from __future__ import annotations

from meridian_assistant.analytics import StructuredResult
from meridian_assistant.contracts import CoverageResult
from meridian_assistant.retrieval import (
    NarrativeEvidence,
    NarrativeFilters,
    RetrievalResult,
    RetrievalStatus,
)
from meridian_assistant.synthesis import (
    EvidenceBundle,
    SourceProvenance,
    TemplateSynthesisAdapter,
)


def _bundle(
    *,
    profile: str = "structured_summary",
    rows: list[dict[str, object]] | None = None,
    narratives: tuple[NarrativeEvidence, ...] = (),
) -> EvidenceBundle:
    retrieval = (
        RetrievalResult(RetrievalStatus.OK, "query", NarrativeFilters(), narratives)
        if narratives
        else None
    )
    mode = "combined" if rows and narratives else "structured" if rows else "narrative"
    return EvidenceBundle(
        "q",
        "question",
        mode,  # type: ignore[arg-type]
        CoverageResult(True, None, None, None),
        None,
        StructuredResult(rows, "SELECT", (), "db") if rows is not None else None,
        retrieval,
        SourceProvenance("db"),
        synthesis_profile=profile,  # type: ignore[arg-type]
    )


def test_template_synthesis_uses_only_bounded_structured_row() -> None:
    evidence = _bundle(rows=[{"company": "Acme", "complaint_count": 4}])
    draft = TemplateSynthesisAdapter().synthesize("ignored unrestricted prompt", evidence)
    assert draft.claims[0].template == "structured_row"
    assert draft.claims[0].structured_refs[0].columns == ("company", "complaint_count")


def test_profile_specific_multi_row_templates_are_complete_and_ordered() -> None:
    ranked = _bundle(
        profile="ranked_issues",
        rows=[
            {"issue": value, "complaint_count": 3, "total_complaints": 6, "share": 0.5}
            for value in ("A", "B")
        ],
    )
    distribution = _bundle(
        profile="response_distribution",
        rows=[
            {
                "company_response_to_consumer": value,
                "complaint_count": 1,
                "total_complaints": 2,
                "share": 0.5,
            }
            for value in ("Closed with explanation", "Closed with monetary relief")
        ],
    )
    drivers = _bundle(
        profile="trend_drivers",
        rows=[
            {
                "row_type": "total",
                "issue": None,
                "baseline_complaints": 10,
                "comparison_complaints": 12,
                "absolute_change": 2,
                "percentage_change": 0.2,
            },
            {
                "row_type": "driver",
                "issue": "Access",
                "baseline_complaints": 4,
                "comparison_complaints": 6,
                "absolute_change": 2,
                "percentage_change": 0.5,
            },
        ],
    )
    adapter = TemplateSynthesisAdapter()
    assert [claim.template for claim in adapter.synthesize("", ranked).claims] == [
        "ranked_issue_row",
        "ranked_issue_row",
    ]
    assert [claim.template for claim in adapter.synthesize("", distribution).claims] == [
        "response_category_row",
        "response_category_row",
    ]
    assert [claim.template for claim in adapter.synthesize("", drivers).claims] == [
        "comparison_total",
        "issue_driver",
    ]


def test_sample_themes_are_repeated_metadata_counts_with_three_bounded_examples() -> None:
    narratives = tuple(
        NarrativeEvidence(
            str(index),
            f"Complaint {index} described a distinct exact experience. "
            + (f"Detail {index}. " * 30),
            {
                "issue": "Account access" if index <= 3 else "Fees",
                "sub_issue": "Frozen account" if index <= 3 else "Unexpected fee",
            },
            float(index),
            False,
        )
        for index in range(1, 6)
    )
    draft = TemplateSynthesisAdapter().synthesize(
        "ignored", _bundle(profile="sample_themes", narratives=narratives)
    )
    theme = draft.claims[0]
    assert theme.template == "retrieved_sample_themes"
    assert [
        (item.issue, item.sub_issue, item.retrieved_count) for item in theme.sample_theme_refs
    ] == [
        ("Account access", "Frozen account", 3),
        ("Fees", "Unexpected fee", 2),
    ]
    examples = [claim for claim in draft.claims if claim.template == "sample_complaint"]
    assert len(examples) == 3
    assert all(len(claim.narrative_refs[0].quote) <= 280 for claim in examples)
    assert any("not population prevalence" in text for text in draft.limitations)
