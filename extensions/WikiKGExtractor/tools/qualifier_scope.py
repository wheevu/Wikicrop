"""Conservative season/condition scope comparisons without rewriting claims.

There are no approved exclusion rules in this policy, so different labels never
produce DISJOINT. Exact non-empty literals can match, but only the frozen
worker's four season labels support multi-value containment or intersection.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from enum import Enum
from typing import Any

POLICY_VERSION = "wikikg.qualifier-scope.v1"
SCOPED_FIELDS = ("season", "condition")
SEASONS = frozenset({"đông xuân", "hè thu", "thu đông", "mùa"})


class ScopeRelation(Enum):
    MATCH = "match"
    COVERS = "covers"
    OVERLAP = "overlap"
    UNKNOWN = "unknown"
    DISJOINT = "disjoint"


def _season_tokens(value: str) -> set[str] | None:
    tokens = {
        " ".join(unicodedata.normalize("NFC", token).split()).casefold()
        for token in value.split(";")
    }
    return tokens if tokens and tokens <= SEASONS else None


def _field_relation(field: str, left: Any, right: Any) -> ScopeRelation:
    if (not isinstance(left, str) or not isinstance(right, str)
            or not left.strip() or not right.strip()):
        return ScopeRelation.UNKNOWN
    if field == "condition":
        # Free text is opaque; string inequality cannot establish exclusion.
        return ScopeRelation.MATCH if left == right else ScopeRelation.UNKNOWN
    left_seasons, right_seasons = _season_tokens(left), _season_tokens(right)
    if left_seasons is None or right_seasons is None:
        # Preserve literal matching without inferring the meaning of unknown labels.
        if left == right and ";" not in left:
            return ScopeRelation.MATCH
        return ScopeRelation.UNKNOWN
    if left_seasons == right_seasons:
        return ScopeRelation.MATCH
    if right_seasons <= left_seasons:
        return ScopeRelation.COVERS
    if left_seasons & right_seasons:
        return ScopeRelation.OVERLAP
    return ScopeRelation.UNKNOWN


def compare_scopes(left: Mapping[str, Any], right: Mapping[str, Any]) -> ScopeRelation:
    """Compare left scope to right; COVERS means left covers all right labels.

    Missing, empty, differently worded, or partially applicable scope is not a
    complete match. Neither omitted fields nor other qualifier keys prove
    disjointness. DISJOINT is reserved for an explicit approved exclusion rule;
    this policy has none.
    """
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return ScopeRelation.UNKNOWN
    relations = []
    for field in SCOPED_FIELDS:
        if field not in left and field not in right:
            continue
        if field not in left or field not in right:
            return ScopeRelation.UNKNOWN
        relations.append(_field_relation(field, left[field], right[field]))
    if ScopeRelation.UNKNOWN in relations:
        return ScopeRelation.UNKNOWN
    if ScopeRelation.OVERLAP in relations:
        return ScopeRelation.OVERLAP
    if ScopeRelation.COVERS in relations:
        return ScopeRelation.COVERS
    return ScopeRelation.MATCH
