"""
Step 5 post-processing:
- Build an internal plan draft from planner nodes.
- Validate/sanitize draft activities with directive-aware checks.
- Map validated draft back into API-compatible ItineraryNode rows.
"""

from __future__ import annotations

from datetime import date, timedelta
from dataclasses import asdict, dataclass, field
import re

from models import EvidenceItem, InputDirectives, ItineraryNode

MAX_ACTIVITY_DURATION_MINS = 12 * 60
MIN_ACTIVITY_DURATION_MINS = 10
DEFAULT_ACTIVITY_TYPE = "sightseeing"
DEFAULT_ACTIVITY_DURATION_MINS = 90
DEFAULT_DAY_START_MINS = 9 * 60      # used only when backfilling a MISSING start time
EARLIEST_ALLOWED_START_MINS = 5 * 60  # hard floor: explicit model times are respected down to 5am

_WAKE_START_MAP = {
    "early_bird": 7 * 60,   # 07:00
    "standard":   9 * 60,   # 09:00
    "late_riser": 10 * 60,  # 10:00
}


def _wake_to_day_start_mins(directives: InputDirectives) -> int:
    """Return the day-start minute derived from wake_time_pref, falling back to DEFAULT_DAY_START_MINS."""
    return _WAKE_START_MAP.get(directives.wake_time_pref or "", DEFAULT_DAY_START_MINS)
DEFAULT_ACTIVITY_GAP_MINS = 30
DEFAULT_DAY_END_MINS = 22 * 60
NIGHT_START_MINS = 19 * 60
NIGHT_END_MINS = 6 * 60
TIME_DURATION_TOLERANCE_MINS = 15
MIN_GAP_FOR_TRANSFER_MINS = 45
MIN_GAP_FOR_FREE_TIME_MINS = 120
MORNING_FILL_THRESHOLD_MINS = 120
EVENING_FILL_THRESHOLD_MINS = 90
DEFAULT_TRANSFER_DURATION_MINS = 30
DEFAULT_FREE_BLOCK_MINS = 120
DEFAULT_EVENING_REST_MINS = 120
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@dataclass
class PlanActivity:
    title: str
    activity_type: str
    duration_mins: int | None
    date_local: str | None
    start_time_local: str | None
    end_time_local: str | None
    lat: float | None
    long: float | None
    description: str | None
    order_index: int
    segment_origin: str = "model"
    segment_kind: str = "activity"
    source_evidence_ids: list[str] = field(default_factory=list)
    validation_notes: list[str] = field(default_factory=list)


@dataclass
class PlanDraft:
    activities: list[PlanActivity]
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


@dataclass
class PlanValidationResult:
    plan: PlanDraft
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", text.lower())}


def _match_evidence_ids(activity: ItineraryNode, evidence: list[EvidenceItem]) -> list[str]:
    activity_blob = f"{activity.title} {activity.description or ''}"
    activity_tokens = _tokenize(activity_blob)
    if not activity_tokens:
        return []

    matches: list[str] = []
    for item in evidence:
        evidence_blob = " ".join(
            [
                item.summary,
                item.source_ref,
                " ".join(item.citations),
                " ".join(item.facts.time_hints),
                " ".join(item.facts.constraints),
                " ".join(item.facts.vibe_tags),
            ]
        )
        evidence_tokens = _tokenize(evidence_blob)
        if activity_tokens.intersection(evidence_tokens):
            matches.append(item.id)
        if len(matches) >= 4:
            break
    return matches


def build_plan_draft(
    *,
    planner_nodes: list[ItineraryNode],
    evidence: list[EvidenceItem],
) -> PlanDraft:
    activities: list[PlanActivity] = []
    for index, node in enumerate(planner_nodes):
        activities.append(
            PlanActivity(
                title=node.title.strip(),
                activity_type=node.activity_type.strip(),
                duration_mins=node.duration_mins,
                date_local=node.date_local,
                start_time_local=node.start_time_local,
                end_time_local=node.end_time_local,
                lat=node.lat,
                long=node.long,
                description=node.description.strip() if node.description else None,
                segment_origin=node.segment_origin or "model",
                segment_kind=node.segment_kind or "activity",
                order_index=index,
                source_evidence_ids=_match_evidence_ids(node, evidence),
            )
        )
    return PlanDraft(activities=activities)


def _enforce_duration_bounds(activity: PlanActivity, warnings: list[str]) -> None:
    if activity.duration_mins is None:
        return

    if activity.duration_mins <= 0:
        warnings.append(
            f"Activity '{activity.title}' had non-positive duration and was reset to null."
        )
        activity.duration_mins = None
        return

    if activity.duration_mins > MAX_ACTIVITY_DURATION_MINS:
        warnings.append(
            (
                f"Activity '{activity.title}' had duration {activity.duration_mins} "
                f"and was clamped to {MAX_ACTIVITY_DURATION_MINS}."
            )
        )
        activity.duration_mins = MAX_ACTIVITY_DURATION_MINS


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _normalize_time_str(value: str) -> str:
    """Normalize H:MM to HH:MM so the strict pattern accepts LLM output like '8:00'."""
    if re.fullmatch(r"^\d:[0-5]\d$", value):
        return f"0{value}"
    return value


def _parse_time_mins(value: str | None) -> int | None:
    if not value:
        return None
    value = _normalize_time_str(value.strip())
    if not TIME_PATTERN.fullmatch(value):
        return None
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _format_time_mins(total_mins: int) -> str:
    clamped = max(0, min(total_mins, 23 * 60 + 59))
    hours = clamped // 60
    minutes = clamped % 60
    return f"{hours:02d}:{minutes:02d}"


def _activity_blob(activity: PlanActivity) -> str:
    return f"{activity.title} {activity.description or ''}".lower()


def _is_night_window(start_mins: int | None, end_mins: int | None) -> bool:
    if start_mins is None or end_mins is None:
        return False
    return (
        start_mins >= NIGHT_START_MINS
        or end_mins >= NIGHT_START_MINS
        or start_mins < NIGHT_END_MINS
        or end_mins < NIGHT_END_MINS
    )


def _contains_no_night_driving_directive(directives: InputDirectives) -> bool:
    blob = " ".join([*directives.hard_constraints, *directives.avoid]).lower()
    return any(
        phrase in blob
        for phrase in (
            "no night driving",
            "no-night driving",
            "avoid night driving",
            "before sunset",
            "avoid driving after dark",
        )
    )


def _normalize_avoid_terms(directives: InputDirectives) -> list[str]:
    avoid_terms: list[str] = []
    for term in directives.avoid:
        normalized = term.strip().lower()
        if not normalized:
            continue
        avoid_terms.append(normalized)
    return avoid_terms


def _matches_avoid_terms(activity: PlanActivity, avoid_terms: list[str]) -> str | None:
    if not avoid_terms:
        return None

    blob = _activity_blob(activity)
    for avoid_term in avoid_terms:
        if avoid_term and avoid_term in blob:
            return avoid_term
    return None


def _reconcile_duration_with_times(activity: PlanActivity, warnings: list[str]) -> None:
    start_mins = _parse_time_mins(activity.start_time_local)
    end_mins = _parse_time_mins(activity.end_time_local)

    if start_mins is None or end_mins is None or end_mins <= start_mins:
        return

    computed_duration = end_mins - start_mins
    if computed_duration < MIN_ACTIVITY_DURATION_MINS:
        computed_duration = MIN_ACTIVITY_DURATION_MINS
        activity.end_time_local = _format_time_mins(start_mins + computed_duration)
        warnings.append(
            (
                f"Activity '{activity.title}' had too-short duration from times and was raised to "
                f"{MIN_ACTIVITY_DURATION_MINS} minutes."
            )
        )

    if activity.duration_mins is None:
        activity.duration_mins = computed_duration
        activity.validation_notes.append("Duration inferred from start/end times.")
        return

    if abs(activity.duration_mins - computed_duration) > TIME_DURATION_TOLERANCE_MINS:
        warnings.append(
            (
                f"Activity '{activity.title}' had duration {activity.duration_mins} that did not match "
                f"start/end ({computed_duration}); duration was corrected."
            )
        )
        activity.duration_mins = computed_duration


def _normalize_activity_schedule(activity: PlanActivity, warnings: list[str]) -> None:
    if activity.date_local and not _parse_iso_date(activity.date_local):
        warnings.append(
            f"Activity '{activity.title}' had invalid date '{activity.date_local}' and it was cleared."
        )
        activity.date_local = None

    # Normalize H:MM → HH:MM in-place so stored values are always zero-padded.
    if activity.start_time_local:
        activity.start_time_local = _normalize_time_str(activity.start_time_local.strip())
    if activity.end_time_local:
        activity.end_time_local = _normalize_time_str(activity.end_time_local.strip())

    start_mins = _parse_time_mins(activity.start_time_local)
    end_mins = _parse_time_mins(activity.end_time_local)

    if activity.start_time_local and start_mins is None:
        warnings.append(
            f"Activity '{activity.title}' had invalid start_time_local '{activity.start_time_local}' and it was cleared."
        )
        activity.start_time_local = None

    if activity.end_time_local and end_mins is None:
        warnings.append(
            f"Activity '{activity.title}' had invalid end_time_local '{activity.end_time_local}' and it was cleared."
        )
        activity.end_time_local = None

    start_mins = _parse_time_mins(activity.start_time_local)
    end_mins = _parse_time_mins(activity.end_time_local)
    if start_mins is not None and end_mins is not None and end_mins <= start_mins:
        fallback_duration = activity.duration_mins or DEFAULT_ACTIVITY_DURATION_MINS
        activity.end_time_local = _format_time_mins(start_mins + fallback_duration)
        warnings.append(
            (
                f"Activity '{activity.title}' had end time before start time; "
                "end_time_local was adjusted using duration."
            )
        )


def _auto_assign_schedule(
    activities: list[PlanActivity],
    *,
    start_date: date | None,
    end_date: date | None,
    directives: InputDirectives,
) -> None:
    if not activities:
        return

    has_any_schedule = any(
        activity.date_local or activity.start_time_local or activity.end_time_local
        for activity in activities
    )
    if not has_any_schedule and start_date is None:
        return

    current_date = start_date
    if current_date is None:
        date_candidates = [
            parsed
            for parsed in (_parse_iso_date(activity.date_local) for activity in activities)
            if parsed is not None
        ]
        current_date = min(date_candidates) if date_candidates else None

    day_start_mins = _wake_to_day_start_mins(directives)
    current_start_mins = day_start_mins

    for activity in sorted(activities, key=lambda item: item.order_index):
        parsed_date = _parse_iso_date(activity.date_local)
        if parsed_date:
            current_date = parsed_date
        elif current_date:
            activity.date_local = current_date.isoformat()
            activity.validation_notes.append("Auto-assigned date from trip schedule context.")

        parsed_start = _parse_time_mins(activity.start_time_local)
        if parsed_start is None:
            parsed_start = current_start_mins
            activity.start_time_local = _format_time_mins(parsed_start)
            activity.validation_notes.append("Auto-assigned start time.")

        parsed_end = _parse_time_mins(activity.end_time_local)
        if parsed_end is None or parsed_end <= parsed_start:
            duration = activity.duration_mins or DEFAULT_ACTIVITY_DURATION_MINS
            parsed_end = parsed_start + duration
            activity.end_time_local = _format_time_mins(parsed_end)
            activity.validation_notes.append("Auto-assigned end time from start + duration.")

        next_start = parsed_end + DEFAULT_ACTIVITY_GAP_MINS
        if next_start > DEFAULT_DAY_END_MINS:
            if current_date:
                next_date = current_date + timedelta(days=1)
                if end_date and next_date > end_date:
                    next_date = end_date
                current_date = next_date
            current_start_mins = day_start_mins
        else:
            current_start_mins = next_start


def _enforce_trip_window_bounds(
    activities: list[PlanActivity],
    *,
    start_date: date | None,
    end_date: date | None,
    warnings: list[str],
) -> None:
    if not start_date and not end_date:
        return

    for activity in activities:
        parsed_date = _parse_iso_date(activity.date_local)
        if not parsed_date:
            continue

        if start_date and parsed_date < start_date:
            warnings.append(
                (
                    f"Activity '{activity.title}' was outside trip window "
                    f"({activity.date_local}) and was moved to {start_date.isoformat()}."
                )
            )
            activity.date_local = start_date.isoformat()
            continue

        if end_date and parsed_date > end_date:
            warnings.append(
                (
                    f"Activity '{activity.title}' was outside trip window "
                    f"({activity.date_local}) and was moved to {end_date.isoformat()}."
                )
            )
            activity.date_local = end_date.isoformat()


def _enforce_daily_timeline_consistency(
    activities: list[PlanActivity],
    *,
    directives: InputDirectives,
    warnings: list[str],
) -> None:
    if not activities:
        return

    no_night_driving = _contains_no_night_driving_directive(directives)
    activities_by_date: dict[str, list[PlanActivity]] = {}

    for activity in activities:
        if not activity.date_local:
            continue
        activities_by_date.setdefault(activity.date_local, []).append(activity)

    day_start_mins = _wake_to_day_start_mins(directives)
    for _, daily_activities in sorted(activities_by_date.items()):
        current_start_mins = day_start_mins
        ordered = sorted(
            daily_activities,
            key=lambda item: (
                _parse_time_mins(item.start_time_local) if item.start_time_local else 99_999,
                item.order_index,
            ),
        )

        for activity in ordered:
            duration = activity.duration_mins or DEFAULT_ACTIVITY_DURATION_MINS
            duration = max(MIN_ACTIVITY_DURATION_MINS, min(duration, MAX_ACTIVITY_DURATION_MINS))
            activity.duration_mins = duration

            start_mins = _parse_time_mins(activity.start_time_local)
            end_mins = _parse_time_mins(activity.end_time_local)

            if start_mins is None:
                start_mins = current_start_mins
                activity.start_time_local = _format_time_mins(start_mins)
                activity.validation_notes.append(
                    "Start time backfilled during strict timeline validation."
                )

            if start_mins < EARLIEST_ALLOWED_START_MINS:
                warnings.append(
                    (
                        f"Activity '{activity.title}' started before {_format_time_mins(EARLIEST_ALLOWED_START_MINS)} "
                        f"and was shifted to {_format_time_mins(EARLIEST_ALLOWED_START_MINS)}."
                    )
                )
                start_mins = EARLIEST_ALLOWED_START_MINS
                activity.start_time_local = _format_time_mins(start_mins)

            if start_mins < current_start_mins:
                warnings.append(
                    (
                        f"Activity '{activity.title}' overlapped earlier activity "
                        "and was shifted later."
                    )
                )
                start_mins = current_start_mins
                activity.start_time_local = _format_time_mins(start_mins)

            if end_mins is None or end_mins <= start_mins:
                end_mins = start_mins + duration
                activity.end_time_local = _format_time_mins(end_mins)
                activity.validation_notes.append(
                    "End time recalculated from start + duration during strict timeline validation."
                )

            _reconcile_duration_with_times(activity, warnings)
            duration = activity.duration_mins or duration
            end_mins = _parse_time_mins(activity.end_time_local) or (start_mins + duration)

            if no_night_driving and activity.activity_type == "transport" and _is_night_window(
                start_mins, end_mins
            ):
                adjusted_end = min(NIGHT_START_MINS - 1, DEFAULT_DAY_END_MINS)
                adjusted_start = max(day_start_mins, adjusted_end - duration)
                if adjusted_end > adjusted_start:
                    start_mins = adjusted_start
                    end_mins = adjusted_end
                    activity.start_time_local = _format_time_mins(start_mins)
                    activity.end_time_local = _format_time_mins(end_mins)
                    activity.duration_mins = end_mins - start_mins
                    warnings.append(
                        (
                            f"Transport activity '{activity.title}' was shifted to daytime "
                            "to satisfy no-night-driving constraint."
                        )
                    )

            if end_mins > DEFAULT_DAY_END_MINS:
                clipped_end = DEFAULT_DAY_END_MINS
                if clipped_end > start_mins:
                    end_mins = clipped_end
                    activity.end_time_local = _format_time_mins(end_mins)
                    activity.duration_mins = end_mins - start_mins
                    warnings.append(
                        (
                            f"Activity '{activity.title}' exceeded day window and was clipped "
                            f"to end at {_format_time_mins(DEFAULT_DAY_END_MINS)}."
                        )
                    )

            current_start_mins = end_mins + DEFAULT_ACTIVITY_GAP_MINS


def _warn_sparse_daily_plan(
    activities: list[PlanActivity],
    *,
    start_date: date | None,
    end_date: date | None,
    warnings: list[str],
) -> None:
    if not activities:
        return

    dated = [activity for activity in activities if _parse_iso_date(activity.date_local)]
    if not dated:
        return

    counts_by_day: dict[str, int] = {}
    for activity in dated:
        counts_by_day[activity.date_local or ""] = counts_by_day.get(activity.date_local or "", 0) + 1

    max_per_day = max(counts_by_day.values())
    avg_per_day = len(dated) / max(len(counts_by_day), 1)
    trip_days = None
    if start_date and end_date:
        trip_days = (end_date - start_date).days + 1

    if len(dated) >= 4 and max_per_day <= 1:
        warnings.append(
            (
                "Plan is sparse (at most one activity per planned day). "
                "Prompt may need stronger full-day chunking instructions."
            )
        )
    elif avg_per_day < 1.7 and trip_days and trip_days >= 3:
        warnings.append(
            "Plan has low daily density; consider requesting 3-5 activities per day."
        )


def _iter_trip_dates(start_date: date, end_date: date) -> list[str]:
    dates: list[str] = []
    current = start_date
    while current <= end_date:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def _resolve_day_keys_for_fill(
    activities: list[PlanActivity],
    *,
    start_date: date | None,
    end_date: date | None,
) -> list[str]:
    if start_date and end_date and start_date <= end_date:
        return _iter_trip_dates(start_date, end_date)

    unique_dates = {
        activity.date_local
        for activity in activities
        if activity.date_local and _parse_iso_date(activity.date_local)
    }
    return sorted(unique_dates)


def _clean_location_label(value: str) -> str:
    cleaned = re.sub(
        r"^(return from|return to|travel from|travel to|transfer from|transfer to|go to|visit)\s+",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:,.")
    return cleaned or value.strip()


def _infer_home_base_title(activities: list[PlanActivity]) -> str | None:
    for activity in sorted(activities, key=lambda item: item.order_index):
        blob = _activity_blob(activity)
        if activity.activity_type == "accommodation":
            return _clean_location_label(activity.title)
        if any(term in blob for term in ("hotel", "accommodation", "check-in", "hostel")):
            return _clean_location_label(activity.title)
    return None


def _build_transfer_title(previous_title: str, next_title: str) -> str:
    prev = _clean_location_label(previous_title)
    nxt = _clean_location_label(next_title)
    if prev.lower() == nxt.lower():
        return f"Transit near {nxt}"
    return f"Transit: {prev} to {nxt}"


def _build_synthetic_activity(
    *,
    title: str,
    activity_type: str,
    segment_kind: str,
    date_local: str,
    start_mins: int,
    end_mins: int,
    description: str,
    order_index: int,
) -> PlanActivity:
    normalized_start = max(0, min(start_mins, 23 * 60 + 59))
    normalized_end = max(normalized_start + MIN_ACTIVITY_DURATION_MINS, end_mins)
    duration = normalized_end - normalized_start
    return PlanActivity(
        title=title,
        activity_type=activity_type,
        duration_mins=duration,
        date_local=date_local,
        start_time_local=_format_time_mins(normalized_start),
        end_time_local=_format_time_mins(normalized_end),
        lat=None,
        long=None,
        description=description,
        segment_origin="synthetic",
        segment_kind=segment_kind,
        order_index=order_index,
        source_evidence_ids=[],
        validation_notes=[
            "Auto-generated continuity segment to reduce blank gaps in the daily plan."
        ],
    )


def _synthesize_daily_completeness_segments(
    activities: list[PlanActivity],
    *,
    start_date: date | None,
    end_date: date | None,
    directives: InputDirectives,
    warnings: list[str],
) -> None:
    day_keys = _resolve_day_keys_for_fill(
        activities,
        start_date=start_date,
        end_date=end_date,
    )
    if not day_keys:
        return

    by_day: dict[str, list[PlanActivity]] = {}
    for activity in activities:
        if activity.date_local and _parse_iso_date(activity.date_local):
            by_day.setdefault(activity.date_local, []).append(activity)

    home_base = _infer_home_base_title(activities)
    next_order_index = (
        max((activity.order_index for activity in activities), default=-1) + 1
    )
    synthesized: list[PlanActivity] = []

    def add_segment(
        *,
        title: str,
        activity_type: str,
        segment_kind: str,
        day_key: str,
        start_mins: int,
        end_mins: int,
        description: str,
    ) -> None:
        nonlocal next_order_index
        if end_mins - start_mins < MIN_ACTIVITY_DURATION_MINS:
            return
        synthesized.append(
            _build_synthetic_activity(
                title=title,
                activity_type=activity_type,
                segment_kind=segment_kind,
                date_local=day_key,
                start_mins=start_mins,
                end_mins=end_mins,
                description=description,
                order_index=next_order_index,
            )
        )
        next_order_index += 1

    for day_key in day_keys:
        day_items = sorted(
            by_day.get(day_key, []),
            key=lambda item: (
                _parse_time_mins(item.start_time_local) if item.start_time_local else 99_999,
                item.order_index,
            ),
        )

        if not day_items:
            add_segment(
                title="Flexible local exploration",
                activity_type="relaxation",
                segment_kind="buffer",
                day_key=day_key,
                start_mins=10 * 60,
                end_mins=12 * 60,
                description=(
                    "Open block for neighborhood exploration, cafe stops, or spontaneous activities."
                ),
            )
            add_segment(
                title=f"Evening rest{f' at {home_base}' if home_base else ''}",
                activity_type="accommodation" if home_base else "relaxation",
                segment_kind="rest",
                day_key=day_key,
                start_mins=18 * 60,
                end_mins=20 * 60,
                description="Buffer block to recharge before the next day.",
            )
            continue

        first = day_items[0]
        first_start = _parse_time_mins(first.start_time_local)
        if first_start is not None:
            morning_slot_start = _wake_to_day_start_mins(directives)
            morning_slot_end = first_start - DEFAULT_ACTIVITY_GAP_MINS
            morning_available = morning_slot_end - morning_slot_start
            if morning_available >= MORNING_FILL_THRESHOLD_MINS:
                add_segment(
                    title="Flexible morning buffer",
                    activity_type="relaxation",
                    segment_kind="buffer",
                    day_key=day_key,
                    start_mins=morning_slot_start,
                    end_mins=min(morning_slot_end, morning_slot_start + DEFAULT_FREE_BLOCK_MINS),
                    description=(
                        "Optional recovery time, breakfast, and light local exploration before the main plan."
                    ),
                )

        for previous, following in zip(day_items, day_items[1:]):
            previous_end = _parse_time_mins(previous.end_time_local)
            following_start = _parse_time_mins(following.start_time_local)
            if previous_end is None or following_start is None:
                continue

            slot_start = previous_end + DEFAULT_ACTIVITY_GAP_MINS
            slot_end = following_start - DEFAULT_ACTIVITY_GAP_MINS
            available = slot_end - slot_start
            if available < MIN_ACTIVITY_DURATION_MINS:
                continue

            if available >= MIN_GAP_FOR_TRANSFER_MINS:
                transfer_end = slot_start + min(DEFAULT_TRANSFER_DURATION_MINS, available)
                add_segment(
                    title=_build_transfer_title(previous.title, following.title),
                    activity_type="transport",
                    segment_kind="transfer",
                    day_key=day_key,
                    start_mins=slot_start,
                    end_mins=transfer_end,
                    description="Explicit transit segment inserted to keep the timeline coherent.",
                )
                slot_start = transfer_end + DEFAULT_ACTIVITY_GAP_MINS
                available = slot_end - slot_start

            if available >= MIN_GAP_FOR_FREE_TIME_MINS:
                add_segment(
                    title=f"Free time near {following.title}",
                    activity_type="relaxation",
                    segment_kind="buffer",
                    day_key=day_key,
                    start_mins=slot_start,
                    end_mins=min(slot_end, slot_start + DEFAULT_FREE_BLOCK_MINS),
                    description=(
                        "Open block for snacks, photos, shopping, or short spontaneous stops."
                    ),
                )

        last = day_items[-1]
        last_end = _parse_time_mins(last.end_time_local)
        if last_end is not None:
            evening_slot_start = last_end + DEFAULT_ACTIVITY_GAP_MINS
            evening_slot_end = DEFAULT_DAY_END_MINS
            evening_available = evening_slot_end - evening_slot_start
            if evening_available >= EVENING_FILL_THRESHOLD_MINS:
                cursor = evening_slot_start
                if (
                    last.activity_type != "accommodation"
                    and home_base
                    and evening_available >= MIN_GAP_FOR_TRANSFER_MINS + MIN_ACTIVITY_DURATION_MINS
                ):
                    transfer_duration = min(
                        DEFAULT_TRANSFER_DURATION_MINS,
                        evening_slot_end - cursor - MIN_ACTIVITY_DURATION_MINS,
                    )
                    if transfer_duration >= MIN_ACTIVITY_DURATION_MINS:
                        transfer_end = cursor + transfer_duration
                        add_segment(
                            title=_build_transfer_title(last.title, home_base),
                            activity_type="transport",
                            segment_kind="transfer",
                            day_key=day_key,
                            start_mins=cursor,
                            end_mins=transfer_end,
                            description=(
                                "Return segment added so the day ends at accommodation context."
                            ),
                        )
                        cursor = transfer_end + DEFAULT_ACTIVITY_GAP_MINS

                rest_end = min(evening_slot_end, cursor + DEFAULT_EVENING_REST_MINS)
                if rest_end - cursor >= MIN_ACTIVITY_DURATION_MINS:
                    add_segment(
                        title=f"Evening wind-down{f' at {home_base}' if home_base else ''}",
                        activity_type="accommodation" if home_base else "relaxation",
                        segment_kind="rest",
                        day_key=day_key,
                        start_mins=cursor,
                        end_mins=rest_end,
                        description=(
                            "Reserved downtime for recovery, packing, and preparation for the next day."
                        ),
                    )

    if synthesized:
        activities.extend(synthesized)
        warnings.append(
            (
                f"Inserted {len(synthesized)} auto-generated transition/rest segments "
                "to improve day completeness."
            )
        )


def _apply_directive_annotations(
    activity: PlanActivity,
    directives: InputDirectives,
) -> None:
    combined_constraints = " ".join(
        [*directives.hard_constraints, *directives.avoid]
    ).lower()

    if (
        ("before sunset" in combined_constraints or "no night driving" in combined_constraints)
        and activity.activity_type == "transport"
    ):
        activity.validation_notes.append(
            "Scheduling note: align transport segments before sunset per user constraints."
        )


def _aggregate_evidence_signals(
    activity: PlanActivity,
    evidence_by_id: dict[str, EvidenceItem],
) -> tuple[list[str], list[str]]:
    time_hints: list[str] = []
    constraints: list[str] = []

    for evidence_id in activity.source_evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if not item:
            continue
        if item.source_type != "web":
            continue
        time_hints.extend(item.facts.time_hints)
        constraints.extend(item.facts.constraints)

    # Ordered dedupe
    time_hints_unique = list(dict.fromkeys(hint for hint in time_hints if hint))
    constraints_unique = list(
        dict.fromkeys(constraint for constraint in constraints if constraint)
    )
    return time_hints_unique, constraints_unique


def _apply_grounded_evidence_annotations(
    activity: PlanActivity,
    evidence_by_id: dict[str, EvidenceItem],
) -> None:
    time_hints, constraints = _aggregate_evidence_signals(activity, evidence_by_id)

    for hint in time_hints[:2]:
        activity.validation_notes.append(f"Grounded timing hint: {hint}")

    for constraint in constraints[:2]:
        activity.validation_notes.append(f"Grounded constraint: {constraint}")


def _time_priority_score(
    activity: PlanActivity,
    evidence_by_id: dict[str, EvidenceItem],
) -> int:
    time_hints, _ = _aggregate_evidence_signals(activity, evidence_by_id)
    hints_blob = " ".join(time_hints).lower()

    score = 0
    if "morning" in hints_blob or "early" in hints_blob:
        score -= 1
    if "night" in hints_blob or "late" in hints_blob:
        score += 1
    if "sunset" in hints_blob and activity.activity_type == "transport":
        score -= 1
    return score


def _enforce_time_window_ordering(
    activities: list[PlanActivity],
    evidence_by_id: dict[str, EvidenceItem],
    warnings: list[str],
) -> list[PlanActivity]:
    if len(activities) <= 1:
        return activities

    with_scores = [
        (activity, _time_priority_score(activity, evidence_by_id)) for activity in activities
    ]
    reordered = [
        activity
        for activity, _ in sorted(
            with_scores,
            key=lambda pair: (pair[1], pair[0].order_index),
        )
    ]

    before = [activity.title for activity in activities]
    after = [activity.title for activity in reordered]
    if before != after:
        warnings.append(
            "Activities were reordered to better satisfy grounded time-window hints."
        )
        for index, activity in enumerate(reordered):
            activity.order_index = index

    return reordered


def _chronological_key(activity: PlanActivity) -> tuple[int, int, int]:
    parsed_date = _parse_iso_date(activity.date_local)
    parsed_start = _parse_time_mins(activity.start_time_local)
    date_key = parsed_date.toordinal() if parsed_date else 999_999_999
    time_key = parsed_start if parsed_start is not None else 99_999
    return date_key, time_key, activity.order_index


def _sort_chronologically(
    activities: list[PlanActivity],
    warnings: list[str],
) -> list[PlanActivity]:
    if len(activities) <= 1:
        return activities

    reordered = sorted(activities, key=_chronological_key)
    before = [activity.title for activity in activities]
    after = [activity.title for activity in reordered]
    if before != after:
        warnings.append("Activities were sorted by date/time in chronological order.")

    for index, activity in enumerate(reordered):
        activity.order_index = index
    return reordered


def validate_plan_draft(
    *,
    plan: PlanDraft,
    directives: InputDirectives,
    evidence: list[EvidenceItem],
    start_date: date | None = None,
    end_date: date | None = None,
) -> PlanValidationResult:
    warnings: list[str] = []
    errors: list[str] = []
    evidence_by_id = {item.id: item for item in evidence}
    avoid_terms = _normalize_avoid_terms(directives)

    deduped: list[PlanActivity] = []
    seen_titles: set[str] = set()

    for activity in plan.activities:
        if not activity.title:
            errors.append("Encountered activity with empty title and dropped it.")
            continue

        normalized_title = activity.title.strip().lower()
        if normalized_title in seen_titles:
            warnings.append(f"Duplicate activity title '{activity.title}' was removed.")
            continue
        seen_titles.add(normalized_title)

        matched_avoid = _matches_avoid_terms(activity, avoid_terms)
        if matched_avoid:
            warnings.append(
                (
                    f"Activity '{activity.title}' matched avoid directive "
                    f"('{matched_avoid}') and was removed."
                )
            )
            continue

        if not activity.activity_type:
            warnings.append(
                f"Activity '{activity.title}' had empty type and defaulted to {DEFAULT_ACTIVITY_TYPE}."
            )
            activity.activity_type = DEFAULT_ACTIVITY_TYPE

        _enforce_duration_bounds(activity, warnings)
        _normalize_activity_schedule(activity, warnings)
        _reconcile_duration_with_times(activity, warnings)
        _apply_directive_annotations(activity, directives)
        _apply_grounded_evidence_annotations(activity, evidence_by_id)
        deduped.append(activity)

    deduped = _enforce_time_window_ordering(deduped, evidence_by_id, warnings)
    _enforce_trip_window_bounds(
        deduped,
        start_date=start_date,
        end_date=end_date,
        warnings=warnings,
    )
    _auto_assign_schedule(deduped, start_date=start_date, end_date=end_date, directives=directives)
    _enforce_trip_window_bounds(
        deduped,
        start_date=start_date,
        end_date=end_date,
        warnings=warnings,
    )
    _enforce_daily_timeline_consistency(
        deduped,
        directives=directives,
        warnings=warnings,
    )
    _synthesize_daily_completeness_segments(
        deduped,
        start_date=start_date,
        end_date=end_date,
        directives=directives,
        warnings=warnings,
    )
    _enforce_daily_timeline_consistency(
        deduped,
        directives=directives,
        warnings=warnings,
    )
    deduped = _sort_chronologically(deduped, warnings)
    _warn_sparse_daily_plan(
        deduped,
        start_date=start_date,
        end_date=end_date,
        warnings=warnings,
    )

    must_include_lower = [item.strip().lower() for item in directives.must_include if item.strip()]
    if must_include_lower:
        titles_lower = [activity.title.strip().lower() for activity in deduped]
        for must_include in must_include_lower:
            if not any(must_include in title for title in titles_lower):
                warnings.append(
                    f"Must-include item '{must_include}' is not explicitly present in the plan."
                )

    if not deduped:
        warnings.append("Validated plan has no activities.")

    validated_plan = PlanDraft(
        activities=deduped,
        warnings=list({*plan.warnings, *warnings}),
        assumptions=plan.assumptions,
    )
    return PlanValidationResult(plan=validated_plan, errors=errors, warnings=warnings)


def map_plan_draft_to_nodes(plan: PlanDraft) -> list[ItineraryNode]:
    nodes: list[ItineraryNode] = []
    for activity in sorted(plan.activities, key=lambda item: item.order_index):
        nodes.append(
            ItineraryNode(
                title=activity.title,
                activity_type=activity.activity_type,
                duration_mins=activity.duration_mins,
                date_local=activity.date_local,
                start_time_local=activity.start_time_local,
                end_time_local=activity.end_time_local,
                lat=activity.lat,
                long=activity.long,
                description=activity.description or None,
                segment_origin=activity.segment_origin,
                segment_kind=activity.segment_kind,
            )
        )
    return nodes


def plan_validation_as_dict(result: PlanValidationResult) -> dict:
    return {
        "errors": result.errors,
        "warnings": result.warnings,
        "plan": {
            "activities": [asdict(activity) for activity in result.plan.activities],
            "warnings": result.plan.warnings,
            "assumptions": result.plan.assumptions,
        },
    }
