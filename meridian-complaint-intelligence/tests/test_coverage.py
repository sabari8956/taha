from __future__ import annotations

import json
from datetime import date

import pytest

from meridian_assistant.contracts import AnswerRecord, DataCapabilities
from meridian_assistant.coverage import check_coverage, read_capabilities, resolve_company


def capabilities() -> DataCapabilities:
    return DataCapabilities("2024-01-01", "2025-12-31", has_consumer_narratives=True)


def manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "row_count": 3,
        "distinct_complaint_id_count": 3,
        "narrative_count": 3,
        "population_criteria": {"date_received": {"start": "2024-01-01", "end": "2025-12-31"}},
    }
    payload.update(overrides)
    return payload


def test_read_manifest_and_reject_2023_request(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_payload()))
    result = check_coverage(
        read_capabilities(manifest),
        requested_start=date(2023, 3, 1),
        requested_end=date(2023, 4, 1),
    )
    assert not result.answerable
    assert result.required_capability == "date_range"
    assert "2024-01-01" in result.reason


def test_relief_and_auto_insurance_require_explicit_capabilities() -> None:
    relief = check_coverage(capabilities(), requires_verified_relief_amount=True)
    insurance = check_coverage(capabilities(), requires_auto_insurance_claims=True)
    assert not relief.answerable
    assert relief.required_capability == "verified_relief_amount"
    assert not insurance.answerable
    assert insurance.required_capability == "auto_insurance_claims"


def test_company_resolution_distinguishes_alias_legal_and_absent_entity() -> None:
    cash_app = resolve_company(" Cash App ")
    legal = resolve_company("Block, Inc.")
    absent = resolve_company("State Farm")
    assert (cash_app.status, cash_app.brand_name) == ("curated_alias", "Cash App")
    assert (legal.status, legal.legal_company) == ("normalized_legal_name", "Block, Inc.")
    assert absent.status == "unresolved"


def test_date_end_is_exclusive_and_answer_record_enforces_abstention_contract() -> None:
    covered = check_coverage(
        capabilities(), requested_start=date(2025, 12, 1), requested_end=date(2026, 1, 1)
    )
    assert covered.answerable
    record = AnswerRecord("q04", "Out of range.", (), None, abstained=True)
    assert record.to_dict()["citations"] == []
    with pytest.raises(ValueError, match="abstained"):
        AnswerRecord("q04", "Out of range.", (123,), None, abstained=True)
    for citation in (True, False):
        with pytest.raises(ValueError, match="numeric"):
            AnswerRecord("q01", "Grounded.", (citation,), None, abstained=False)


@pytest.mark.parametrize(
    ("missing_field", "payload", "message"),
    [
        ("population_criteria", None, "population_criteria"),
        (None, {"population_criteria": {}}, "date_received"),
        (
            None,
            {"population_criteria": {"date_received": {"start": "bad", "end": "2025-12-31"}}},
            "ISO date",
        ),
        (
            None,
            {
                "population_criteria": {
                    "date_received": {"start": "2025-12-31", "end": "2024-01-01"}
                }
            },
            "start must not exceed",
        ),
        (None, {"narrative_count": True}, "narrative_count"),
        (None, {"narrative_count": "3"}, "narrative_count"),
        (None, {"row_count": True}, "row_count"),
        (None, {"distinct_complaint_id_count": -1}, "distinct_complaint_id_count"),
    ],
)
def test_malformed_manifest_fails_closed(tmp_path, missing_field, payload, message) -> None:
    manifest = tmp_path / "manifest.json"
    document = manifest_payload(**(payload or {}))
    if missing_field is not None:
        document.pop(missing_field)
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=message):
        read_capabilities(manifest)


def test_nondefault_snapshot_boundaries_appear_in_reason() -> None:
    result = check_coverage(
        DataCapabilities("2022-05-01", "2022-06-30", has_consumer_narratives=True),
        requested_start=date(2022, 4, 30),
        requested_end=date(2022, 5, 2),
    )
    assert not result.answerable
    assert "2022-05-01" in result.reason

    result = check_coverage(
        DataCapabilities("2022-05-01", "2022-06-30", has_consumer_narratives=True),
        requested_start=date(2022, 6, 30),
        requested_end=date(2022, 7, 2),
    )
    assert not result.answerable
    assert "2022-06-30" in result.reason
