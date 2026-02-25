"""
NovaSync — Pydantic models for request / response validation.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class InputDirectives(BaseModel):
    """User steering constraints/preferences consumed by orchestration."""

    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    mobility_mode: str | None = None
    budget_level: str | None = None
    pace: str | None = None


# ── Request ─────────────────────────────────────────────────────────────────
class ProcessIdeaRequest(BaseModel):
    idea: str = Field(..., min_length=1, description="Raw, unstructured travel idea dump")
    trip_id: str | None = Field(
        default=None,
        description="Existing trip UUID. If omitted, a new trip is created automatically.",
    )
    trip_location: str | None = Field(
        default=None,
        description="Primary trip location (city/region/country).",
    )
    start_date: date | None = Field(
        default=None,
        description="Trip start date in YYYY-MM-DD format.",
    )
    end_date: date | None = Field(
        default=None,
        description="Trip end date in YYYY-MM-DD format.",
    )
    trip_window_mode: Literal["fixed", "not_decided"] = Field(
        default="fixed",
        description="Date selection mode. fixed=start/end provided, not_decided=derive from trip_days.",
    )
    trip_days: int | None = Field(
        default=None,
        ge=1,
        le=60,
        description="Trip length in days when exact dates are not decided.",
    )
    timezone: str | None = Field(
        default=None,
        description="IANA timezone (for example, Australia/Hobart).",
    )
    links: list[str] = Field(
        default_factory=list,
        description="Optional list of social/web links to use as context",
    )
    input_directives: InputDirectives = Field(
        default_factory=InputDirectives,
        description="Optional explicit constraints/preferences for itinerary planning",
    )

    @model_validator(mode="after")
    def validate_trip_window(self) -> "ProcessIdeaRequest":
        has_start = self.start_date is not None
        has_end = self.end_date is not None

        if has_start != has_end:
            raise ValueError(
                "Provide both `start_date` and `end_date`, or leave both empty and provide `trip_days`."
            )

        if has_start and has_end and self.start_date > self.end_date:
            raise ValueError("`start_date` must be earlier than or equal to `end_date`.")

        # If exact dates are not provided, derive a deterministic date window from trip length.
        if not has_start and not has_end:
            if self.trip_days is None:
                raise ValueError(
                    "Trip dates are required. Provide `start_date` and `end_date`, "
                    "or choose not decided mode and provide `trip_days`."
                )

            derived_start = date.today()
            derived_end = derived_start + timedelta(days=max(1, self.trip_days) - 1)
            self.start_date = derived_start
            self.end_date = derived_end
            self.trip_window_mode = "not_decided"

        return self


# ── Domain ──────────────────────────────────────────────────────────────────
class ItineraryNode(BaseModel):
    title: str
    activity_type: str
    duration_mins: int | None = None
    date_local: str | None = None
    start_time_local: str | None = None
    end_time_local: str | None = None
    lat: float | None = None
    long: float | None = None
    description: str | None = None
    segment_origin: Literal["model", "synthetic"] | None = None
    segment_kind: Literal["activity", "transfer", "buffer", "rest"] | None = None


# ── Response ────────────────────────────────────────────────────────────────
class ProcessIdeaResponse(BaseModel):
    trip_id: str
    nodes: list[ItineraryNode]
    planner_scaffold_text: str | None = None


# ── Internal Orchestration Models ───────────────────────────────────────────
class EvidenceFacts(BaseModel):
    locations: list[str] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    time_hints: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    vibe_tags: list[str] = Field(default_factory=list)


class EvidenceDebug(BaseModel):
    fetch_status: str | None = None
    page_title: str | None = None
    content_excerpt: str | None = None
    parsed_text_preview: str | None = None
    raw_text_preview: str | None = None
    parsed_text_full: str | None = None
    raw_text_full: str | None = None
    llm_condensed_preview: str | None = None
    llm_condensed_full: str | None = None
    llm_summary_model: str | None = None
    llm_summary_error: str | None = None
    llm_summary_trace: dict[str, object] | None = None
    time_hint_sentences: list[str] = Field(default_factory=list)
    constraint_sentences: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    id: str
    source_type: Literal[
        "upload_image",
        "upload_video",
        "instagram",
        "tiktok",
        "web",
        "user_text",
    ]
    source_ref: str
    summary: str
    facts: EvidenceFacts = Field(default_factory=EvidenceFacts)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    citations: list[str] = Field(default_factory=list)
    raw_artifact_ref: str | None = None
    debug: EvidenceDebug | None = None
