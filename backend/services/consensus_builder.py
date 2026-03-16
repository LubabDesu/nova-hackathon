"""
NovaSync — consensus builder for group trip preferences.

Pure Python, no LLM calls. Merges multiple TravelerProfiles into a
single InputDirectives using conservative merge strategies per field.
"""

from __future__ import annotations

from models import ConflictItem, ConsensusResult, InputDirectives, TravelerProfile

# Conservative ordering (lower index = more conservative / restrictive)
_BUDGET_ORDER = ["budget", "mid_range", "luxury"]
_PACE_ORDER = ["relaxed", "moderate", "active"]
_FITNESS_ORDER = ["low", "moderate", "high"]
_WAKE_ORDER = ["early_bird", "standard", "late_riser"]   # latest riser = most conservative
_ACCOMMODATION_ORDER = ["budget", "mid_range", "boutique", "luxury"]


def _most_conservative(values: list[str | None], order: list[str]) -> str | None:
    """Return the value that appears earliest (most conservative) in the order list."""
    present = [v for v in values if v is not None and v in order]
    if not present:
        return None
    return min(present, key=lambda v: order.index(v))


def _union_lists(lists: list[list[str]]) -> list[str]:
    """Union of multiple string lists with case-insensitive deduplication."""
    seen: set[str] = set()
    result: list[str] = []
    for lst in lists:
        for item in lst:
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(item.strip())
    return result


def build_consensus(profiles: list[TravelerProfile]) -> ConsensusResult:
    """
    Merge traveler profiles into a single InputDirectives.

    - List fields: union (all constraints honored)
    - Scalar fields: most conservative value wins
    - Scalar fields with >1 distinct non-None value → ConflictItem added
    - Planning always proceeds regardless of conflicts
    """
    if not profiles:
        return ConsensusResult(
            merged_directives=InputDirectives(),
            conflicts=[],
            traveler_profiles=[],
        )

    conflicts: list[ConflictItem] = []

    # ── Union fields ──────────────────────────────────────────────────────────
    merged = InputDirectives(
        hard_constraints=_union_lists([p.extracted_directives.hard_constraints for p in profiles]),
        soft_preferences=_union_lists([p.extracted_directives.soft_preferences for p in profiles]),
        must_include=_union_lists([p.extracted_directives.must_include for p in profiles]),
        avoid=_union_lists([p.extracted_directives.avoid for p in profiles]),
        dietary=_union_lists([p.extracted_directives.dietary for p in profiles]),
        travel_party=[],  # meaningless to merge across travelers
    )

    # ── Conservative scalar merge + conflict detection ────────────────────────
    def _merge_scalar(
        field: str,
        order: list[str],
        getter,
    ) -> str | None:
        per_traveler = [(p.nickname, getter(p)) for p in profiles]
        non_none = [(nick, v) for nick, v in per_traveler if v is not None]
        distinct = list({v for _, v in non_none})
        if len(distinct) > 1:
            val_travelers: dict[str, list[str]] = {}
            for nick, v in non_none:
                val_travelers.setdefault(v, []).append(nick)
            conflicts.append(ConflictItem(
                field=field,
                values=distinct,
                travelers=[nick for nick, _ in non_none],
            ))
        return _most_conservative([v for _, v in non_none], order)

    merged.budget_level = _merge_scalar(
        "budget_level", _BUDGET_ORDER,
        lambda p: p.extracted_directives.budget_level,
    )
    merged.pace = _merge_scalar(
        "pace", _PACE_ORDER,
        lambda p: p.extracted_directives.pace,
    )
    merged.fitness_level = _merge_scalar(
        "fitness_level", _FITNESS_ORDER,
        lambda p: p.extracted_directives.fitness_level,
    )
    merged.wake_time_pref = _merge_scalar(
        "wake_time_pref", _WAKE_ORDER,
        lambda p: p.extracted_directives.wake_time_pref,
    )
    merged.accommodation_style = _merge_scalar(
        "accommodation_style", _ACCOMMODATION_ORDER,
        lambda p: p.extracted_directives.accommodation_style,
    )

    # mobility_mode: use it only if all travelers agree; otherwise None + conflict
    mobility_values = [(p.nickname, p.extracted_directives.mobility_mode) for p in profiles]
    non_none_mobility = [(n, v) for n, v in mobility_values if v is not None]
    distinct_mobility = list({v for _, v in non_none_mobility})
    if len(distinct_mobility) == 1:
        merged.mobility_mode = distinct_mobility[0]
    elif len(distinct_mobility) > 1:
        conflicts.append(ConflictItem(
            field="mobility_mode",
            values=distinct_mobility,
            travelers=[n for n, _ in non_none_mobility],
        ))
        merged.mobility_mode = None
    else:
        merged.mobility_mode = None

    return ConsensusResult(
        merged_directives=merged,
        conflicts=conflicts,
        traveler_profiles=profiles,
    )
