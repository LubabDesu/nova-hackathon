"""
NovaSync — normalize raw request inputs into internal evidence items.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from models import EvidenceFacts, EvidenceItem, InputDirectives
from services.openrouter import MediaInput


def _truncate(value: str, max_len: int = 240) -> str:
    text = value.strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len - 1]}…"


def _link_source_type(link: str) -> str:
    host = urlparse(link).netloc.lower()
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "web"


def _has_directives(directives: InputDirectives) -> bool:
    return bool(
        directives.hard_constraints
        or directives.soft_preferences
        or directives.must_include
        or directives.avoid
        or directives.mobility_mode
        or directives.budget_level
        or directives.pace
    )


def build_initial_evidence(
    *,
    idea_text: str,
    trip_location: str | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str | None,
    links: list[str],
    media_inputs: list[MediaInput],
    input_directives: InputDirectives,
) -> list[EvidenceItem]:
    """
    Build early-stage evidence records directly from user-provided inputs.

    These are intentionally lightweight placeholders for downstream workers.
    """

    evidence: list[EvidenceItem] = [
        EvidenceItem(
            id="ev_user_prompt_1",
            source_type="user_text",
            source_ref="user_prompt",
            summary=_truncate(idea_text),
            facts=EvidenceFacts(),
            confidence=1.0,
        )
    ]

    if trip_location or start_date or end_date or timezone:
        trip_window_parts: list[str] = []
        if start_date:
            trip_window_parts.append(start_date.isoformat())
        if end_date:
            trip_window_parts.append(end_date.isoformat())
        trip_window = " to ".join(trip_window_parts)

        summary_parts: list[str] = []
        if trip_location:
            summary_parts.append(f"location={trip_location}")
        if trip_window:
            summary_parts.append(f"window={trip_window}")
        if timezone:
            summary_parts.append(f"timezone={timezone}")

        evidence.append(
            EvidenceItem(
                id="ev_trip_context_1",
                source_type="user_text",
                source_ref="trip_context",
                summary=_truncate(
                    "Trip context provided: "
                    + ", ".join(summary_parts)
                ),
                facts=EvidenceFacts(
                    locations=[trip_location] if trip_location else [],
                    time_hints=[f"Trip dates: {trip_window}"] if trip_window else [],
                    constraints=[f"Plan within trip window: {trip_window}"]
                    if trip_window
                    else [],
                    vibe_tags=[f"timezone:{timezone}"] if timezone else [],
                ),
                confidence=1.0,
            )
        )

    if _has_directives(input_directives):
        constraints: list[str] = [
            *input_directives.hard_constraints,
            *[f"avoid: {item}" for item in input_directives.avoid],
        ]
        preferences: list[str] = [
            *input_directives.soft_preferences,
            *[f"must_include: {item}" for item in input_directives.must_include],
        ]
        meta: list[str] = []
        if input_directives.mobility_mode:
            meta.append(f"mobility_mode: {input_directives.mobility_mode}")
        if input_directives.budget_level:
            meta.append(f"budget_level: {input_directives.budget_level}")
        if input_directives.pace:
            meta.append(f"pace: {input_directives.pace}")

        evidence.append(
            EvidenceItem(
                id="ev_user_directives_1",
                source_type="user_text",
                source_ref="input_directives",
                summary=_truncate(
                    "; ".join(constraints + preferences + meta)
                    or "User provided planning directives."
                ),
                facts=EvidenceFacts(
                    constraints=constraints,
                    vibe_tags=preferences + meta,
                ),
                confidence=1.0,
            )
        )

    for index, link in enumerate(links, start=1):
        source_type = _link_source_type(link)
        evidence.append(
            EvidenceItem(
                id=f"ev_link_{index}",
                source_type=source_type,
                source_ref=link,
                summary=f"User supplied {source_type} link for itinerary context.",
                facts=EvidenceFacts(),
                confidence=0.7,
                citations=[link],
            )
        )

    for index, media in enumerate(media_inputs, start=1):
        source_type = "upload_video" if media.mime_type.startswith("video/") else "upload_image"
        evidence.append(
            EvidenceItem(
                id=f"ev_media_{index}",
                source_type=source_type,
                source_ref=media.filename,
                summary=f"User uploaded {source_type.replace('_', ' ')}: {media.filename}",
                facts=EvidenceFacts(),
                confidence=0.75,
            )
        )

    return evidence
