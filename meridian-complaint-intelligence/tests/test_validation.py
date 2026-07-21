from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from meridian_assistant.analytics import StructuredResult
from meridian_assistant.contracts import CoverageResult
from meridian_assistant.retrieval import (
    NarrativeEvidence,
    NarrativeFilters,
    RetrievalResult,
    RetrievalStatus,
)
from meridian_assistant.synthesis import (
    DraftClaim,
    EvidenceBundle,
    NarrativeReference,
    SampleThemeReference,
    SourceProvenance,
    StructuredReference,
    SynthesisDraft,
)
from meridian_assistant.validation import render_validated, validate_draft


def _evidence() -> EvidenceBundle:
    filters = NarrativeFilters(
        company="Acme",
        brand_name="Acme Pay",
        product="Payments",
        sub_product="Wallet",
        issue="Frozen",
        sub_issue="Access",
        state="CA",
        timely_response=False,
        company_response="Closed with explanation",
        start=date(2025, 1, 1),
        end=date(2025, 2, 1),
    )
    metadata = {
        "company": "Acme",
        "brand_name": "Acme Pay",
        "product": "Payments",
        "sub_product": "Wallet",
        "issue": "Frozen",
        "sub_issue": "Access",
        "state": "CA",
        "timely_response": "false",
        "company_response": "Closed with explanation",
        "date_received": "2025-01-15",
    }
    return EvidenceBundle(
        "q",
        "question",
        "combined",
        CoverageResult(True, None, "2024-01-01", "2025-12-31"),
        None,
        StructuredResult([{"company": "Acme", "complaint_count": 3}], "SELECT 3", (), "db"),
        RetrievalResult(
            RetrievalStatus.OK,
            "query",
            filters,
            (NarrativeEvidence("42", "exact returned excerpt", metadata, 0.1, False),),
        ),
        SourceProvenance("db"),
    )


def _draft() -> SynthesisDraft:
    return SynthesisDraft(
        (
            DraftClaim("quantitative", "structured_row", (StructuredReference(0, ("company",)),)),
            DraftClaim(
                "narrative",
                "attributed_complaint",
                narrative_refs=(NarrativeReference("42", "returned excerpt"),),
            ),
        )
    )


def test_valid_typed_claims_pass() -> None:
    result = validate_draft(_evidence(), _draft())
    assert result.accepted
    assert "Complaint 42 reported" in render_validated(_evidence(), _draft())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("company", "Other"),
        ("brand_name", "Other Pay"),
        ("product", "Mortgage"),
        ("company_response", "Closed with monetary relief"),
        ("timely_response", "true"),
        ("date_received", "2025-02-01"),
    ],
)
def test_every_executed_filter_mismatch_rejects(field: str, value: str) -> None:
    evidence = _evidence()
    item = evidence.retrieval.evidence[0]
    changed = replace(item, metadata=item.metadata | {field: value})
    evidence = replace(evidence, retrieval=replace(evidence.retrieval, evidence=(changed,)))
    assert not validate_draft(evidence, _draft()).accepted


def test_missing_filter_metadata_rejects() -> None:
    evidence = _evidence()
    item = evidence.retrieval.evidence[0]
    metadata = dict(item.metadata)
    metadata.pop("state")
    evidence = replace(
        evidence,
        retrieval=replace(evidence.retrieval, evidence=(replace(item, metadata=metadata),)),
    )
    assert not validate_draft(evidence, _draft()).accepted


@pytest.mark.parametrize(
    ("field", "value"),
    [("company", "Other"), ("timely_response", "true")],
)
def test_uncited_returned_item_filter_mismatch_rejects(field: str, value: str) -> None:
    evidence = _evidence()
    cited = evidence.retrieval.evidence[0]
    uncited = replace(
        cited,
        complaint_id="43",
        excerpt="uncited returned excerpt",
        metadata=cited.metadata | {field: value},
    )
    evidence = replace(
        evidence,
        retrieval=replace(evidence.retrieval, evidence=(cited, uncited)),
    )

    result = validate_draft(evidence, _draft())

    assert not result.accepted
    assert result.accepted_citation_ids == ()
    assert f"Complaint ID '43' mismatches filter {field}" in result.reasons


@pytest.mark.parametrize(
    "claim",
    [
        DraftClaim("causal", "attributed_complaint"),  # type: ignore[arg-type]
        DraftClaim("narrative", "caused_population_harm"),
        DraftClaim("quantitative", "structured_row"),
        DraftClaim("limitation", "limitation", narrative_refs=(NarrativeReference("42", "x"),)),
    ],
)
def test_unknown_causal_or_malformed_claims_reject_without_rendering(claim: DraftClaim) -> None:
    result = validate_draft(_evidence(), SynthesisDraft((claim,)))
    assert not result.accepted


def _profile_evidence(profile: str, rows: list[dict[str, object]]) -> EvidenceBundle:
    return EvidenceBundle(
        "q",
        "question",
        "structured",
        CoverageResult(True, None, None, None),
        None,
        StructuredResult(rows, "SELECT", (), "db"),
        None,
        SourceProvenance("db"),
        synthesis_profile=profile,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    ("profile", "rows", "claims"),
    [
        (
            "ranked_issues",
            [{"issue": "A", "complaint_count": 2, "total_complaints": 2, "share": 1.0}],
            [DraftClaim("quantitative", "structured_row", (StructuredReference(0, ("issue",)),))],
        ),
        (
            "response_distribution",
            [
                {
                    "company_response_to_consumer": "Closed with explanation",
                    "complaint_count": 2,
                    "total_complaints": 2,
                    "share": 1.0,
                }
            ],
            [
                DraftClaim(
                    "quantitative",
                    "ranked_issue_row",
                    (StructuredReference(0, ("complaint_count",)),),
                )
            ],
        ),
        (
            "trend_drivers",
            [
                {
                    "row_type": "total",
                    "issue": None,
                    "baseline_complaints": 1,
                    "comparison_complaints": 2,
                    "absolute_change": 1,
                    "percentage_change": 1.0,
                },
                {
                    "row_type": "driver",
                    "issue": "A",
                    "baseline_complaints": 1,
                    "comparison_complaints": 2,
                    "absolute_change": 1,
                    "percentage_change": 1.0,
                },
            ],
            [
                DraftClaim(
                    "quantitative",
                    "issue_driver",
                    (StructuredReference(0, ("row_type", "issue")),),
                ),
                DraftClaim(
                    "quantitative",
                    "comparison_total",
                    (StructuredReference(1, ("row_type",)),),
                ),
            ],
        ),
    ],
)
def test_profile_specific_generic_or_mismatched_templates_reject(
    profile: str, rows: list[dict[str, object]], claims: list[DraftClaim]
) -> None:
    assert not validate_draft(
        _profile_evidence(profile, rows), SynthesisDraft(tuple(claims))
    ).accepted


@pytest.mark.parametrize(
    ("profile", "template", "rows", "invalid_indexes"),
    [
        (
            "ranked_issues",
            "ranked_issue_row",
            [
                {"issue": issue, "complaint_count": 1, "total_complaints": 2, "share": 0.5}
                for issue in ("A", "B")
            ],
            ((0,), (0, 0), (1, 0)),
        ),
        (
            "response_distribution",
            "response_category_row",
            [
                {
                    "company_response_to_consumer": category,
                    "complaint_count": 1,
                    "total_complaints": 2,
                    "share": 0.5,
                }
                for category in ("Explanation", "Monetary")
            ],
            ((0,), (0, 0), (1, 0)),
        ),
        (
            "trend_drivers",
            "issue_driver",
            [
                {
                    "row_type": row_type,
                    "issue": issue,
                    "baseline_complaints": 1,
                    "comparison_complaints": 2,
                    "absolute_change": 1,
                    "percentage_change": 1.0,
                }
                for row_type, issue in (("total", None), ("driver", "A"), ("driver", "B"))
            ],
            ((0, 1), (0, 1, 1), (0, 2, 1)),
        ),
    ],
)
def test_profile_duplicate_missing_and_reordered_rows_reject(
    profile: str,
    template: str,
    rows: list[dict[str, object]],
    invalid_indexes: tuple[tuple[int, ...], ...],
) -> None:
    evidence = _profile_evidence(profile, rows)
    columns = tuple(rows[0])
    for indexes in invalid_indexes:
        claims = tuple(
            DraftClaim(
                "quantitative",
                "comparison_total" if profile == "trend_drivers" and index == 0 else template,
                (StructuredReference(index, columns),),
            )
            for index in indexes
        )
        assert not validate_draft(evidence, SynthesisDraft(claims)).accepted


@pytest.mark.parametrize(
    ("profile", "template", "row", "columns"),
    [
        (
            "ranked_issues",
            "ranked_issue_row",
            {"issue": "A", "complaint_count": 2, "total_complaints": 2, "share": 1.0},
            ("issue", "complaint_count", "total_complaints", "share"),
        ),
        (
            "response_distribution",
            "response_category_row",
            {
                "company_response_to_consumer": "Explanation",
                "complaint_count": 2,
                "total_complaints": 2,
                "share": 1.0,
            },
            (
                "company_response_to_consumer",
                "complaint_count",
                "total_complaints",
                "share",
            ),
        ),
        (
            "trend_drivers",
            "comparison_total",
            {
                "row_type": "total",
                "issue": None,
                "baseline_complaints": 1,
                "comparison_complaints": 2,
                "absolute_change": 1,
                "percentage_change": 1.0,
            },
            (
                "row_type",
                "issue",
                "baseline_complaints",
                "comparison_complaints",
                "absolute_change",
                "percentage_change",
            ),
        ),
    ],
)
def test_profile_columns_must_match_exact_order_without_extras(
    profile: str,
    template: str,
    row: dict[str, object],
    columns: tuple[str, ...],
) -> None:
    invalid_columns = (columns[:-1], tuple(reversed(columns)), (*columns, "extra"))
    for candidate in invalid_columns:
        changed_row = row | ({"extra": "unexpected"} if "extra" in candidate else {})
        changed_evidence = _profile_evidence(profile, [changed_row])
        draft = SynthesisDraft(
            (DraftClaim("quantitative", template, (StructuredReference(0, candidate),)),)
        )
        assert not validate_draft(changed_evidence, draft).accepted


def test_sample_theme_must_exactly_match_repeated_metadata_and_is_sample_bounded() -> None:
    items = tuple(
        NarrativeEvidence(
            str(index),
            f"exact excerpt {index}",
            {"issue": "Access", "sub_issue": "Frozen"},
            0.1,
            False,
        )
        for index in range(1, 4)
    )
    evidence = EvidenceBundle(
        "q06",
        "question",
        "narrative",
        CoverageResult(True, None, None, None),
        None,
        None,
        RetrievalResult(RetrievalStatus.OK, "query", NarrativeFilters(), items),
        SourceProvenance(),
        synthesis_profile="sample_themes",
    )
    examples = tuple(
        DraftClaim(
            "narrative",
            "sample_complaint",
            narrative_refs=(NarrativeReference(item.complaint_id, item.excerpt),),
        )
        for item in items
    )
    valid = SynthesisDraft(
        (
            DraftClaim(
                "sample_theme",
                "retrieved_sample_themes",
                sample_theme_refs=(SampleThemeReference("Access", "Frozen", 3),),
            ),
            *examples,
        )
    )
    result = validate_draft(evidence, valid)
    assert result.accepted
    rendered = render_validated(evidence, valid)
    assert "Among the retrieved examples" in rendered
    assert "Access / Frozen (3)" in rendered
    fabricated = replace(
        valid,
        claims=(
            replace(
                valid.claims[0],
                sample_theme_refs=(SampleThemeReference("Access", "Frozen", 99),),
            ),
            *examples,
        ),
    )
    assert not validate_draft(evidence, fabricated).accepted

    attributed = replace(
        valid,
        claims=(valid.claims[0], replace(valid.claims[1], template="attributed_complaint")),
    )
    result = validate_draft(evidence, attributed)
    assert not result.accepted
    assert "sample theme narratives require sample_complaint templates" in result.reasons


@pytest.mark.parametrize(
    ("interpretations", "limitations"),
    [
        (("This is the most prevalent theme in the complaint corpus.",), ()),
        ((), ("This theme dominates all complaints in the corpus.",)),
        ((), ("These examples establish population prevalence.",)),
    ],
)
def test_sample_theme_rejects_unrestricted_corpus_prevalence_text(
    interpretations: tuple[str, ...], limitations: tuple[str, ...]
) -> None:
    item = NarrativeEvidence(
        "1",
        "exact excerpt",
        {"issue": "Access", "sub_issue": "Frozen"},
        0.1,
        False,
    )
    evidence = EvidenceBundle(
        "q06",
        "question",
        "narrative",
        CoverageResult(True, None, None, None),
        None,
        None,
        RetrievalResult(
            RetrievalStatus.OK,
            "query",
            NarrativeFilters(),
            (item, replace(item, complaint_id="2")),
        ),
        SourceProvenance(),
        synthesis_profile="sample_themes",
    )
    draft = SynthesisDraft(
        (
            DraftClaim(
                "sample_theme",
                "retrieved_sample_themes",
                sample_theme_refs=(SampleThemeReference("Access", "Frozen", 2),),
            ),
            DraftClaim(
                "narrative",
                "sample_complaint",
                narrative_refs=(NarrativeReference("1", "exact excerpt"),),
            ),
        ),
        interpretations,
        limitations,
    )
    assert not validate_draft(evidence, draft).accepted


def test_sample_theme_accepts_deterministic_allowlisted_limitations() -> None:
    item = NarrativeEvidence(
        "1",
        "exact excerpt",
        {"issue": "Access", "sub_issue": "Frozen"},
        0.1,
        True,
    )
    evidence = EvidenceBundle(
        "q06",
        "question",
        "narrative",
        CoverageResult(True, None, None, None),
        None,
        None,
        RetrievalResult(
            RetrievalStatus.OK,
            "query",
            NarrativeFilters(),
            (item, replace(item, complaint_id="2")),
        ),
        SourceProvenance(),
        synthesis_profile="sample_themes",
    )
    draft = SynthesisDraft(
        (
            DraftClaim(
                "sample_theme",
                "retrieved_sample_themes",
                sample_theme_refs=(SampleThemeReference("Access", "Frozen", 2),),
            ),
            DraftClaim(
                "narrative",
                "sample_complaint",
                narrative_refs=(NarrativeReference("1", "exact excerpt"),),
            ),
        ),
        limitations=(
            "These are relevance-ranked retrieved examples, not population prevalence estimates.",
            "At least one cited narrative was truncated by the index excerpt limit.",
        ),
    )
    assert validate_draft(evidence, draft).accepted


def test_fabricated_references_and_quotes_fail_whole_draft() -> None:
    draft = SynthesisDraft(
        (
            DraftClaim("quantitative", "structured_row", (StructuredReference(3, ("missing",)),)),
            DraftClaim(
                "narrative",
                "attributed_complaint",
                narrative_refs=(NarrativeReference("99", "fabricated"),),
            ),
        )
    )
    result = validate_draft(_evidence(), draft)
    assert not result.accepted
    assert result.accepted_citation_ids == ()
