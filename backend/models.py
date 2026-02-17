"""
NovaSync — Pydantic models for request / response validation.
"""

from __future__ import annotations

from datetime import date
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
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("`start_date` must be earlier than or equal to `end_date`.")
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


# ── Response ────────────────────────────────────────────────────────────────
class ProcessIdeaResponse(BaseModel):
    trip_id: str
    nodes: list[ItineraryNode]


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
