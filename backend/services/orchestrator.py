"""
NovaSync orchestration service.

Runs workers in deterministic order, then calls the planner model.
"""

from __future__ import annotations

from datetime import date
from dataclasses import asdict, dataclass

from models import EvidenceItem, InputDirectives, ItineraryNode
from services.openrouter import MediaInput, extract_itinerary
from services.planning_postprocess import (
    build_plan_draft,
    map_plan_draft_to_nodes,
    plan_validation_as_dict,
    validate_plan_draft,
)
from services.workers.media_worker import run_media_context_worker
from services.workers.url_worker import run_url_context_worker
from services.workers.web_grounding_worker import run_web_grounding_worker
from services.workers.web_research_worker import run_web_research_worker


@dataclass(frozen=True)
class WorkerReport:
    worker_name: str
    status: str
    evidence_added: int
    notes: str | None = None


@dataclass(frozen=True)
class OrchestrationResult:
    nodes: list[ItineraryNode]
    evidence: list[EvidenceItem]
    worker_reports: list[WorkerReport]
    validation_report: dict


def _render_directives_section(input_directives: InputDirectives) -> str:
    lines: list[str] = []
    if input_directives.hard_constraints:
        lines.append("Hard constraints:")
        lines.extend(f"- {item}" for item in input_directives.hard_constraints)
    if input_directives.soft_preferences:
        lines.append("Soft preferences:")
        lines.extend(f"- {item}" for item in input_directives.soft_preferences)
    if input_directives.must_include:
        lines.append("Must include:")
        lines.extend(f"- {item}" for item in input_directives.must_include)
    if input_directives.avoid:
        lines.append("Avoid:")
        lines.extend(f"- {item}" for item in input_directives.avoid)
    if input_directives.mobility_mode:
        lines.append(f"Mobility mode: {input_directives.mobility_mode}")
    if input_directives.budget_level:
        lines.append(f"Budget level: {input_directives.budget_level}")
    if input_directives.pace:
        lines.append(f"Pace: {input_directives.pace}")

    return "\n".join(lines) if lines else "No explicit directives provided."


def _render_evidence_lines(evidence: list[EvidenceItem]) -> str:
    lines: list[str] = []
    for item in evidence[:40]:
        fact_bits: list[str] = []
        if item.facts.constraints:
            fact_bits.append(f"constraints={item.facts.constraints}")
        if item.facts.time_hints:
            fact_bits.append(f"time_hints={item.facts.time_hints}")
        if item.facts.vibe_tags:
            fact_bits.append(f"vibe_tags={item.facts.vibe_tags}")
        if item.citations:
            fact_bits.append(f"citations={item.citations[:2]}")

        suffix = f" | {'; '.join(fact_bits)}" if fact_bits else ""
        lines.append(
            f"- [{item.id}] ({item.source_type}) {item.summary}{suffix}"
        )

    return "\n".join(lines) if lines else "- No evidence available."


def _render_trip_context_section(
    *,
    trip_location: str | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str | None,
) -> str:
    lines: list[str] = []
    if trip_location:
        lines.append(f"Location: {trip_location}")
    if start_date:
        lines.append(f"Start date: {start_date.isoformat()}")
    if end_date:
        lines.append(f"End date: {end_date.isoformat()}")
    if timezone:
        lines.append(f"Timezone: {timezone}")
    return "\n".join(lines) if lines else "No explicit trip context provided."


def _build_planner_prompt(
    *,
    idea_text: str,
    trip_location: str | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str | None,
    input_directives: InputDirectives,
    evidence: list[EvidenceItem],
) -> str:
    return (
        "User travel request:\n"
        f"{idea_text}\n\n"
        "Trip context:\n"
        f"{_render_trip_context_section(trip_location=trip_location, start_date=start_date, end_date=end_date, timezone=timezone)}\n\n"
        "Planning directives:\n"
        f"{_render_directives_section(input_directives)}\n\n"
        "Normalized evidence (including web research):\n"
        f"{_render_evidence_lines(evidence)}\n\n"
        "Planning instructions:\n"
        "- Hard constraints and avoid directives are non-negotiable. Do not output activities that violate them.\n"
        "- If constraints conflict with candidate activities, drop or replace the conflicting activities.\n"
        "- Build coherent full days, not sparse plans. Aim for 3-5 activities per day when trip duration allows.\n"
        "- Avoid one-activity-per-day outputs unless the user explicitly asks for a very relaxed pace.\n"
        "- Keep each day internally coherent with realistic transitions and timing gaps.\n"
        "- Produce date_local (YYYY-MM-DD), start_time_local (HH:MM), and end_time_local (HH:MM) for each activity when possible.\n"
        "- Ensure duration_mins matches the difference between start_time_local and end_time_local.\n"
        "- Use web-research timing constraints when available.\n"
        "- Prefer logically coherent sequencing and realistic durations.\n"
        "- If evidence conflicts, prioritize explicit user directives."
    )


def orchestrate_itinerary_planning(
    *,
    idea_text: str,
    trip_location: str | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str | None,
    links: list[str],
    media_inputs: list[MediaInput],
    input_directives: InputDirectives,
    initial_evidence: list[EvidenceItem],
) -> OrchestrationResult:
    """
    Deterministic orchestration flow (Step 3 + Step 4).
    """
    merged_evidence = list(initial_evidence)
    reports: list[WorkerReport] = []

    if links:
        url_evidence = run_url_context_worker(links)
        merged_evidence.extend(url_evidence)
        reports.append(
            WorkerReport(
                worker_name="url_context_worker",
                status="SUCCESS",
                evidence_added=len(url_evidence),
            )
        )
    else:
        reports.append(
            WorkerReport(
                worker_name="url_context_worker",
                status="SKIPPED",
                evidence_added=0,
                notes="No links provided.",
            )
        )

    if media_inputs:
        media_evidence = run_media_context_worker(media_inputs)
        merged_evidence.extend(media_evidence)
        reports.append(
            WorkerReport(
                worker_name="media_context_worker",
                status="SUCCESS",
                evidence_added=len(media_evidence),
            )
        )
    else:
        reports.append(
            WorkerReport(
                worker_name="media_context_worker",
                status="SKIPPED",
                evidence_added=0,
                notes="No media uploads provided.",
            )
        )

    # Step 4: always run web research worker.
    web_evidence = run_web_research_worker(
        idea_text=idea_text,
        input_directives=input_directives,
    )
    merged_evidence.extend(web_evidence)
    reports.append(
        WorkerReport(
            worker_name="web_research_worker",
            status="SUCCESS",
            evidence_added=len(web_evidence),
        )
    )

    grounded_web_evidence = run_web_grounding_worker(web_evidence)
    merged_evidence.extend(grounded_web_evidence)
    reports.append(
        WorkerReport(
            worker_name="web_grounding_worker",
            status="SUCCESS",
            evidence_added=len(grounded_web_evidence),
            notes="Grounded citations into scheduling facts.",
        )
    )

    planner_prompt = _build_planner_prompt(
        idea_text=idea_text,
        trip_location=trip_location,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        input_directives=input_directives,
        evidence=merged_evidence,
    )
    planner_nodes = extract_itinerary(planner_prompt, media_inputs)
    plan_draft = build_plan_draft(
        planner_nodes=planner_nodes,
        evidence=merged_evidence,
    )
    validation_result = validate_plan_draft(
        plan=plan_draft,
        directives=input_directives,
        evidence=merged_evidence,
        start_date=start_date,
        end_date=end_date,
    )
    nodes = map_plan_draft_to_nodes(validation_result.plan)

    return OrchestrationResult(
        nodes=nodes,
        evidence=merged_evidence,
        worker_reports=reports,
        validation_report=plan_validation_as_dict(validation_result),
    )


def worker_reports_as_dicts(worker_reports: list[WorkerReport]) -> list[dict]:
    return [asdict(report) for report in worker_reports]
