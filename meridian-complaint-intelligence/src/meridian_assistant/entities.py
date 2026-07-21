"""Canonical legal-company and consumer-brand identities.

This registry is the single source used by Silver canonicalization, planning/entity
resolution, retrieval constraints, and RAG metadata construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from meridian_assistant.contracts import EntityResolution


@dataclass(frozen=True)
class CompanyIdentity:
    legal_company: str
    brand_name: str | None
    aliases: tuple[str, ...]


def company_key(value: str) -> str:
    """Return the punctuation-insensitive key stored in Silver metadata."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


COMPANY_IDENTITIES: tuple[CompanyIdentity, ...] = (
    CompanyIdentity("Block, Inc.", "Cash App", ("Cash App",)),
    CompanyIdentity("Early Warning Services, LLC", "Zelle", ("Zelle",)),
    CompanyIdentity("TRANSUNION INTERMEDIATE HOLDINGS, INC.", "TransUnion", ("TransUnion",)),
    CompanyIdentity("MOHELA", "MOHELA", ("MOHELA",)),
)


def _lookup() -> dict[str, tuple[CompanyIdentity, bool]]:
    result: dict[str, tuple[CompanyIdentity, bool]] = {}
    for identity in COMPANY_IDENTITIES:
        result[company_key(identity.legal_company)] = (identity, False)
        for alias in identity.aliases:
            result[company_key(alias)] = (identity, True)
    return result


_LOOKUP = _lookup()
COMPANY_ALIASES = {
    key: identity.brand_name
    for key, (identity, _is_alias) in _LOOKUP.items()
    if identity.brand_name is not None
}


def resolve_company(value: str) -> EntityResolution:
    """Resolve a curated alias or canonical legal name without guessing."""
    cleaned = value.strip()
    match = _LOOKUP.get(company_key(cleaned)) if cleaned else None
    if match is None:
        return EntityResolution(cleaned or value, None, None, "unresolved")
    identity, is_alias = match
    return EntityResolution(
        cleaned,
        identity.legal_company,
        identity.brand_name,
        "curated_alias" if is_alias else "normalized_legal_name",
    )


def brand_case_pairs() -> tuple[tuple[str, str], ...]:
    """Return stable company-key/brand pairs for Silver's CASE expression."""
    return tuple(sorted(COMPANY_ALIASES.items()))
