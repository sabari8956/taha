"""Deterministic validation and rendering for typed synthesis drafts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from meridian_assistant.retrieval import NarrativeEvidence, NarrativeFilters
from meridian_assistant.synthesis import EvidenceBundle, SampleThemeReference, SynthesisDraft

VALIDATOR_VERSION = "draft-validator-v5"
_ALLOWED_TEMPLATES = {
    "quantitative": {
        "structured_row",
        "ranked_issue_row",
        "response_category_row",
        "comparison_total",
        "issue_driver",
        "quarter_comparison_total",
        "quarter_issue_driver",
    },
    "narrative": {"attributed_complaint", "sample_complaint"},
    "sample_theme": {"retrieved_sample_themes"},
    "limitation": {"limitation"},
}
_EXPECTED_COLUMNS = {
    "ranked_issue_row": ("issue", "complaint_count", "total_complaints", "share"),
    "response_category_row": (
        "company_response_to_consumer",
        "complaint_count",
        "total_complaints",
        "share",
    ),
    "comparison_total": (
        "row_type",
        "issue",
        "baseline_complaints",
        "comparison_complaints",
        "absolute_change",
        "percentage_change",
    ),
    "issue_driver": (
        "row_type",
        "issue",
        "baseline_complaints",
        "comparison_complaints",
        "absolute_change",
        "percentage_change",
    ),
    "quarter_comparison_total": (
        "row_type",
        "winner_company",
        "issue",
        "baseline_complaints",
        "comparison_complaints",
        "absolute_change",
        "percentage_change",
    ),
    "quarter_issue_driver": (
        "row_type",
        "winner_company",
        "issue",
        "baseline_complaints",
        "comparison_complaints",
        "absolute_change",
        "percentage_change",
    ),
}
_SAMPLE_THEME_LIMITATIONS = {
    "No issue/sub-issue label repeated among the retrieved examples; only individual "
    "examples are shown.",
    "These are relevance-ranked retrieved examples, not population prevalence estimates.",
    "At least one cited narrative was truncated by the index excerpt limit.",
}
_METADATA_FIELDS = (
    "company",
    "brand_name",
    "product",
    "sub_product",
    "issue",
    "sub_issue",
    "state",
    "company_response",
)


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reasons: tuple[str, ...]
    validator_version: str
    accepted_citation_ids: tuple[str, ...]
    claim_count: int
    template_hash: str


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _template_hash(draft: SynthesisDraft) -> str:
    payload = [(claim.kind, claim.template) for claim in draft.claims]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _metadata_error(item: NarrativeEvidence, filters: NarrativeFilters) -> tuple[str, ...]:
    reasons: list[str] = []
    metadata = item.metadata
    for field in _METADATA_FIELDS:
        expected = getattr(filters, field)
        if expected is None:
            continue
        actual = metadata.get(field)
        if actual is None or not actual.strip():
            reasons.append(f"Complaint ID {item.complaint_id!r} is missing metadata {field}")
        elif _normalized(actual).casefold() != _normalized(expected).casefold():
            reasons.append(f"Complaint ID {item.complaint_id!r} mismatches filter {field}")
    if filters.timely_response is not None:
        actual = metadata.get("timely_response", "").strip().casefold()
        if actual not in {"true", "false"}:
            reasons.append(
                f"Complaint ID {item.complaint_id!r} has invalid timely_response metadata"
            )
        elif (actual == "true") is not filters.timely_response:
            reasons.append(f"Complaint ID {item.complaint_id!r} mismatches filter timely_response")
    if filters.start is not None or filters.end is not None:
        raw_date = metadata.get("date_received", "")
        try:
            actual_date = date.fromisoformat(raw_date)
        except ValueError:
            reasons.append(f"Complaint ID {item.complaint_id!r} has invalid date_received metadata")
        else:
            if filters.start is not None and actual_date < filters.start:
                reasons.append(f"Complaint ID {item.complaint_id!r} predates retrieval start")
            if filters.end is not None and actual_date >= filters.end:
                reasons.append(f"Complaint ID {item.complaint_id!r} is outside retrieval end")
    return tuple(reasons)


def validate_draft(evidence: EvidenceBundle, draft: SynthesisDraft) -> ValidationResult:
    reasons: list[str] = []
    citations: list[str] = []
    if evidence.mode in {"structured", "combined"} and evidence.structured is None:
        reasons.append("required structured evidence is missing")
    if evidence.mode in {"narrative", "combined"} and evidence.retrieval is None:
        reasons.append("required narrative evidence is missing")
    returned_evidence = evidence.retrieval.evidence if evidence.retrieval else ()
    if evidence.retrieval is not None:
        seen_returned_ids: set[str] = set()
        for item in returned_evidence:
            reasons.extend(_metadata_error(item, evidence.retrieval.filters))
            if (
                not item.complaint_id.isdecimal()
                or str(int(item.complaint_id)) != item.complaint_id
            ):
                reasons.append(f"Complaint ID {item.complaint_id!r} is not canonical decimal")
            if item.complaint_id in seen_returned_ids:
                reasons.append(f"Complaint ID {item.complaint_id!r} is duplicated in evidence")
            seen_returned_ids.add(item.complaint_id)
    available = {item.complaint_id: item for item in returned_evidence}
    for claim in draft.claims:
        allowed_templates = _ALLOWED_TEMPLATES.get(claim.kind)
        if allowed_templates is None:
            reasons.append(f"unsupported claim kind: {claim.kind}")
            continue
        if claim.template not in allowed_templates:
            reasons.append(f"claim kind {claim.kind!r} does not allow template {claim.template!r}")
            continue
        if claim.kind == "quantitative":
            if len(claim.structured_refs) != 1 or claim.narrative_refs or claim.sample_theme_refs:
                reasons.append("quantitative claim requires exactly one structured reference")
                continue
            reference = claim.structured_refs[0]
            rows = evidence.structured.rows if evidence.structured else []
            if reference.row_index < 0 or reference.row_index >= len(rows):
                reasons.append("structured row reference does not exist")
                continue
            if not reference.columns:
                reasons.append("structured reference requires at least one column")
            row = rows[reference.row_index]
            missing = set(reference.columns) - set(row)
            if missing:
                reasons.append("structured column reference does not exist")
            expected_columns = _EXPECTED_COLUMNS.get(claim.template)
            if expected_columns is not None and reference.columns != expected_columns:
                reasons.append(f"{claim.template} requires exact ordered structured columns")
            if (
                claim.template in {"comparison_total", "quarter_comparison_total"}
                and row.get("row_type") != "total"
            ):
                reasons.append("comparison total must reference the total row")
            if (
                claim.template in {"issue_driver", "quarter_issue_driver"}
                and row.get("row_type") != "driver"
            ):
                reasons.append("issue driver must reference a driver row")
        elif claim.kind == "narrative":
            if claim.structured_refs or len(claim.narrative_refs) != 1 or claim.sample_theme_refs:
                reasons.append("narrative claim requires exactly one narrative reference")
                continue
            reference = claim.narrative_refs[0]
            item = available.get(reference.complaint_id)
            if item is None:
                reasons.append(f"Complaint ID {reference.complaint_id!r} is not in evidence")
                continue
            if (
                not reference.complaint_id.isdecimal()
                or str(int(reference.complaint_id)) != reference.complaint_id
            ):
                reasons.append(f"Complaint ID {reference.complaint_id!r} is not canonical decimal")
            if reference.complaint_id in citations:
                reasons.append(f"Complaint ID {reference.complaint_id!r} is duplicated")
            else:
                citations.append(reference.complaint_id)
            if not reference.quote or _normalized(reference.quote) not in _normalized(item.excerpt):
                reasons.append(
                    f"quote for Complaint ID {reference.complaint_id!r} is not in excerpt"
                )
        elif claim.kind == "sample_theme":
            if claim.structured_refs or claim.narrative_refs:
                reasons.append("sample theme must not contain row or complaint references")
                continue
            if not 1 <= len(claim.sample_theme_refs) <= 3:
                reasons.append("sample theme requires between one and three repeated labels")
                continue
            if claim.sample_theme_refs != _expected_sample_themes(returned_evidence):
                reasons.append("sample themes do not match repeated retrieved metadata")
        else:
            if claim.structured_refs or claim.narrative_refs or claim.sample_theme_refs:
                reasons.append("limitation claim must not contain evidence references")
            if not claim.template:
                reasons.append("limitation claim template is missing")
    quantitative = [claim for claim in draft.claims if claim.kind == "quantitative"]
    quantitative_sequence = [
        (claim.template, claim.structured_refs[0].row_index)
        if len(claim.structured_refs) == 1
        else (claim.template, None)
        for claim in quantitative
    ]
    if evidence.synthesis_profile == "response_distribution" and evidence.structured:
        expected = [
            ("response_category_row", index) for index in range(len(evidence.structured.rows))
        ]
        if quantitative_sequence != expected:
            reasons.append(
                "response distribution requires response_category_row for every row in order"
            )
    if evidence.synthesis_profile == "ranked_issues" and evidence.structured:
        expected = [
            ("ranked_issue_row", index) for index in range(min(5, len(evidence.structured.rows)))
        ]
        if quantitative_sequence != expected:
            reasons.append("ranked issues require ranked_issue_row for bounded rows in order")
    if evidence.synthesis_profile in {"trend_drivers", "quarter_drivers"} and evidence.structured:
        total_template = (
            "quarter_comparison_total"
            if evidence.synthesis_profile == "quarter_drivers"
            else "comparison_total"
        )
        driver_template = (
            "quarter_issue_driver"
            if evidence.synthesis_profile == "quarter_drivers"
            else "issue_driver"
        )
        expected = [(total_template, 0)] + [
            (driver_template, index) for index in range(1, min(4, len(evidence.structured.rows)))
        ]
        if quantitative_sequence != expected:
            reasons.append(
                "trend drivers require one comparison_total followed by issue_driver rows in order"
            )
    if evidence.synthesis_profile == "sample_themes":
        theme_claims = [claim for claim in draft.claims if claim.kind == "sample_theme"]
        narrative_claims = [claim for claim in draft.claims if claim.kind == "narrative"]
        expected_themes = _expected_sample_themes(returned_evidence)
        if expected_themes and len(theme_claims) != 1:
            reasons.append("sample themes require exactly one repeated-label summary")
        if not expected_themes and theme_claims:
            reasons.append("sample theme summary requires repeated retrieved labels")
        if any(claim.template != "sample_complaint" for claim in narrative_claims):
            reasons.append("sample theme narratives require sample_complaint templates")
        if draft.interpretation_choices:
            reasons.append("sample themes do not allow free-form interpretations")
        if any(limitation not in _SAMPLE_THEME_LIMITATIONS for limitation in draft.limitations):
            reasons.append("sample themes require allowlisted limitations")
    if len(citations) > 3:
        reasons.append("narrative examples exceed the maximum of three citations")
    if evidence.mode in {"narrative", "combined"} and not citations:
        reasons.append("narrative answer has no accepted citations")
    if evidence.structured and evidence.structured.rows:
        row = evidence.structured.rows[0]
        if "timely_response_known_count" in row and not any(
            "unknown" in choice.casefold() for choice in draft.interpretation_choices
        ):
            reasons.append("known-response denominator interpretation is missing")
    return ValidationResult(
        not reasons,
        tuple(dict.fromkeys(reasons)),
        VALIDATOR_VERSION,
        tuple(citations) if not reasons else (),
        len(draft.claims),
        _template_hash(draft),
    )


def _expected_sample_themes(
    evidence: tuple[NarrativeEvidence, ...],
) -> tuple[SampleThemeReference, ...]:
    counts: dict[tuple[str, str | None], int] = {}
    for item in evidence:
        issue = _normalized(item.metadata.get("issue", ""))
        sub_issue = _normalized(item.metadata.get("sub_issue", "")) or None
        if issue:
            key = (issue, sub_issue)
            counts[key] = counts.get(key, 0) + 1
    themes = [
        SampleThemeReference(issue, sub_issue, count)
        for (issue, sub_issue), count in counts.items()
        if count >= 2
    ]
    return tuple(
        sorted(
            themes,
            key=lambda theme: (
                -theme.retrieved_count,
                theme.issue.casefold(),
                (theme.sub_issue or "").casefold(),
            ),
        )[:3]
    )


def _value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def render_validated(evidence: EvidenceBundle, draft: SynthesisDraft) -> str:
    """Render a draft whose exact kind/template/reference matrix was validated."""
    parts: list[str] = []
    for claim in draft.claims:
        if claim.kind == "quantitative":
            reference = claim.structured_refs[0]
            assert evidence.structured is not None
            row = evidence.structured.rows[reference.row_index]
            if claim.template == "ranked_issue_row":
                parts.append(
                    f"{row['issue']}: {_value(row['complaint_count'])} complaints "
                    f"({_value(float(row['share']) * 100)}% of {_value(row['total_complaints'])})."
                )
            elif claim.template == "response_category_row":
                parts.append(
                    f"{row['company_response_to_consumer']}: {_value(row['complaint_count'])} "
                    f"({_value(float(row['share']) * 100)}% of {_value(row['total_complaints'])})."
                )
            elif claim.template in {"comparison_total", "quarter_comparison_total"}:
                direction = (
                    "up"
                    if row["absolute_change"] > 0
                    else "down"
                    if row["absolute_change"] < 0
                    else "flat"
                )
                parts.append(
                    f"Complaints were {direction}: {_value(row['baseline_complaints'])} in the "
                    f"baseline year versus {_value(row['comparison_complaints'])} in the "
                    f"comparison year, a change of {_value(row['absolute_change'])}."
                )
            elif claim.template in {"issue_driver", "quarter_issue_driver"}:
                parts.append(
                    f"Driver {row['issue']}: change {_value(row['absolute_change'])} "
                    f"({_value(row['baseline_complaints'])} to "
                    f"{_value(row['comparison_complaints'])})."
                )
            else:
                values = ", ".join(
                    f"{column}={_value(row[column])}" for column in reference.columns
                )
                parts.append(f"The executed analysis returned: {values}.")
        elif claim.kind == "narrative":
            reference = claim.narrative_refs[0]
            prefix = (
                "Among the retrieved examples, " if claim.template == "sample_complaint" else ""
            )
            parts.append(
                f"{prefix}Complaint {reference.complaint_id} reported: “{reference.quote}”"
            )
        elif claim.kind == "sample_theme":
            labels = []
            for theme in claim.sample_theme_refs:
                label = theme.issue
                if theme.sub_issue:
                    label += f" / {theme.sub_issue}"
                labels.append(f"{label} ({theme.retrieved_count})")
            parts.append(
                "Among the retrieved examples, repeated issue/sub-issue labels were: "
                + "; ".join(labels)
                + "."
            )
        else:
            parts.append("Limitation: " + claim.template)
    if draft.interpretation_choices:
        parts.append("Interpretation: " + " ".join(draft.interpretation_choices))
    if draft.limitations:
        parts.append("Limitations: " + " ".join(draft.limitations))
    return " ".join(parts)
