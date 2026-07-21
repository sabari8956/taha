"""Deterministic source coverage and company-resolution checks.

These functions deliberately inspect only the Bronze manifest. They do not open any
complaint database, retrieve narratives, or infer facts from an LLM.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from meridian_assistant.contracts import CoverageResult, DataCapabilities
from meridian_assistant.entities import resolve_company as _resolve_company

DEFAULT_MANIFEST = Path("bronze/current/manifest.json")


def resolve_company(value: str):
    """Compatibility export for the canonical entity resolver."""
    return _resolve_company(value)


def _manifest_error(message: str) -> ValueError:
    return ValueError(f"invalid Bronze manifest: {message}")


def _required_nonnegative_count(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _manifest_error(f"{field} must be a nonnegative integer")
    return value


def _required_iso_date(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _manifest_error(f"{field} must be an ISO date string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise _manifest_error(f"{field} must be an ISO date string") from error


def read_capabilities(manifest_path: Path = DEFAULT_MANIFEST) -> DataCapabilities:
    """Read and validate the Bronze manifest without opening complaint data.

    Incomplete provenance cannot establish source coverage. Callers must treat this
    explicit validation error as unavailable coverage and abstain rather than guess.
    """
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise _manifest_error("root must be an object")
    criteria = payload.get("population_criteria")
    if not isinstance(criteria, dict):
        raise _manifest_error("population_criteria must be an object")
    date_criteria = criteria.get("date_received")
    if not isinstance(date_criteria, dict):
        raise _manifest_error("population_criteria.date_received must be an object")
    start = _required_iso_date(
        date_criteria.get("start"), "population_criteria.date_received.start"
    )
    end = _required_iso_date(date_criteria.get("end"), "population_criteria.date_received.end")
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise _manifest_error("population_criteria.date_received start must not exceed end")
    _required_nonnegative_count(payload, "row_count")
    _required_nonnegative_count(payload, "distinct_complaint_id_count")
    narrative_count = _required_nonnegative_count(payload, "narrative_count")
    return DataCapabilities(
        snapshot_start=start,
        snapshot_end=end,
        has_consumer_narratives=narrative_count > 0,
    )


def check_coverage(
    capabilities: DataCapabilities,
    *,
    requested_start: date | None = None,
    requested_end: date | None = None,
    requires_verified_relief_amount: bool = False,
    requires_auto_insurance_claims: bool = False,
) -> CoverageResult:
    """Return an explicit answerability decision for hard source constraints.

    End dates are exclusive. Auto-insurance is intentionally unsupported unless a
    future capability registry proves the selected source contains that domain.
    """
    if requires_verified_relief_amount and not capabilities.has_verified_relief_amount:
        return CoverageResult(
            False,
            "The CFPB response category does not contain a verified dollar amount of relief paid.",
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "verified_relief_amount",
        )
    if requires_auto_insurance_claims and not capabilities.supports_auto_insurance_claims:
        return CoverageResult(
            False,
            "This CFPB snapshot does not establish coverage of auto-insurance claims.",
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "auto_insurance_claims",
        )
    if not capabilities.has_consumer_narratives:
        return CoverageResult(
            False,
            "The snapshot contains no consumer narratives.",
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "consumer_narratives",
        )
    if not capabilities.snapshot_start or not capabilities.snapshot_end:
        return CoverageResult(
            False,
            "The snapshot manifest does not establish date coverage.",
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "date_range",
        )
    if requested_start and requested_end and requested_start >= requested_end:
        return CoverageResult(
            False,
            "The requested date range is invalid: start must precede exclusive end.",
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "date_range",
        )
    snapshot_start = (
        date.fromisoformat(capabilities.snapshot_start) if capabilities.snapshot_start else None
    )
    snapshot_end = (
        date.fromisoformat(capabilities.snapshot_end) if capabilities.snapshot_end else None
    )
    if requested_start and snapshot_start and requested_start < snapshot_start:
        return CoverageResult(
            False,
            (
                "The requested period begins before this snapshot's "
                f"{capabilities.snapshot_start} coverage."
            ),
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "date_range",
        )
    if (
        requested_end
        and snapshot_end
        and requested_end > snapshot_end.replace(day=snapshot_end.day) + date.resolution
    ):
        return CoverageResult(
            False,
            (
                "The requested period extends beyond this snapshot's "
                f"{capabilities.snapshot_end} coverage."
            ),
            capabilities.snapshot_start,
            capabilities.snapshot_end,
            "date_range",
        )
    return CoverageResult(True, None, capabilities.snapshot_start, capabilities.snapshot_end)
