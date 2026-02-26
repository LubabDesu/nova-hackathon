"""
NovaSync orchestration service.

Runs workers in deterministic order, then calls the planner model.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from dataclasses import asdict, dataclass
import logging
import os
from typing import Any

from models import EvidenceItem, InputDirectives, ItineraryNode
from services.openrouter import (
    MediaInput,
    MediaSignal,
    build_planning_scaffold,
    critique_planning_scaffold,
    revise_planning_scaffold,
    extract_itinerary,
    extract_media_context,
)
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

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


PLANNER_EVIDENCE_MAX_ITEMS = _env_int("PLANNER_EVIDENCE_MAX_ITEMS", 14)
PLANNER_EVIDENCE_MAX_CHARS = _env_int("PLANNER_EVIDENCE_MAX_CHARS", 5500)
PLANNER_EVIDENCE_LINE_MAX_CHARS = _env_int("PLANNER_EVIDENCE_LINE_MAX_CHARS", 220)
WORKER_INPUT_LOG_PREVIEW_CHARS = _env_int("WORKER_INPUT_LOG_PREVIEW_CHARS", 420)
WORKER_INPUT_LOG_MAX_ITEMS = _env_int("WORKER_INPUT_LOG_MAX_ITEMS", 8)


def _preview_for_log(value: str, max_chars: int = WORKER_INPUT_LOG_PREVIEW_CHARS) -> str:
    compact = " ".join(value.split())
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


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
    planner_scaffold_text: str | None = None
    planner_critique_text: str | None = None
    planner_revised_scaffold_text: str | None = None
    debug_trace: dict[str, Any] | None = None


def _truncate_text(value: str, max_len: int) -> str:
    text = value.strip()
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1]}…"


def _compact_values(values: list[str], *, max_items: int = 3, max_len: int = 36) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if not item:
            continue
        if len(item) > max_len:
            item = f"{item[: max_len - 1]}…"
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
        if len(normalized) >= max_items:
            break
    return ", ".join(normalized)


def _render_lifestyle_section(input_directives: InputDirectives) -> str:
    """Render lifestyle profile fields into a natural language traveller profile paragraph."""
    lines: list[str] = []
    if input_directives.travel_party:
        party_map = {
            "solo": "travelling solo",
            "partner": "travelling with a partner",
            "family_young_kids": "travelling with young children",
            "family_older_kids": "travelling with older children",
            "friends": "travelling with friends",
            "elderly": "travelling with elderly companions",
        }
        described = [party_map.get(p, p) for p in input_directives.travel_party]
        lines.append(f"Group: {', '.join(described)}")
    if input_directives.wake_time_pref:
        wake_map = {
            "early_bird": "early riser (comfortable starting from 07:00)",
            "standard": "standard wake time (starts from 09:00)",
            "late_riser": "late riser (prefers starting from 10:00-11:00)",
        }
        lines.append(f"Schedule: {wake_map.get(input_directives.wake_time_pref, input_directives.wake_time_pref)}")
    if input_directives.dietary:
        lines.append(f"Dietary needs: {', '.join(input_directives.dietary)}")
    if input_directives.fitness_level:
        fitness_map = {
            "low": "low fitness / prefers easy walking, no strenuous hikes",
            "moderate": "moderate fitness / can handle moderate walks and activities",
            "high": "high fitness / comfortable with strenuous activities and long hikes",
        }
        lines.append(f"Fitness: {fitness_map.get(input_directives.fitness_level, input_directives.fitness_level)}")
    if input_directives.accommodation_style:
        lines.append(f"Accommodation preference: {input_directives.accommodation_style}")

    if not lines:
        return ""
    return "Traveller profile:\n" + "\n".join(f"- {line}" for line in lines)


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


def _render_evidence_line(item: EvidenceItem) -> str:
    fact_bits: list[str] = []
    if item.facts.constraints:
        fact_bits.append(f"constraints={_compact_values(item.facts.constraints)}")
    if item.facts.time_hints:
        fact_bits.append(f"time_hints={_compact_values(item.facts.time_hints)}")
    if item.facts.vibe_tags:
        fact_bits.append(f"vibe_tags={_compact_values(item.facts.vibe_tags)}")
    if item.citations:
        fact_bits.append(f"citations={_compact_values(item.citations, max_items=1, max_len=52)}")

    summary = _truncate_text(item.summary, PLANNER_EVIDENCE_LINE_MAX_CHARS)
    suffix = f" | {'; '.join(fact_bits)}" if fact_bits else ""
    return f"- [{item.id}] ({item.source_type}) {summary}{suffix}"


def _render_evidence_lines(evidence: list[EvidenceItem]) -> str:
    lines: list[str] = []
    for item in evidence:
        lines.append(_render_evidence_line(item))

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
    planning_scaffold: str | None,
) -> str:
    scaffold_section = ""
    if planning_scaffold:
        scaffold_section = (
            "Draft itinerary scaffold (model-generated natural language):\n"
            f"{_truncate_text(planning_scaffold, 2600)}\n\n"
        )

    lifestyle_section = _render_lifestyle_section(input_directives)
    lifestyle_block = f"{lifestyle_section}\n\n" if lifestyle_section else ""

    return (
        "User travel request:\n"
        f"{idea_text}\n\n"
        "Trip context:\n"
        f"{_render_trip_context_section(trip_location=trip_location, start_date=start_date, end_date=end_date, timezone=timezone)}\n\n"
        f"{lifestyle_block}"
        "Planning directives:\n"
        f"{_render_directives_section(input_directives)}\n\n"
        "Normalized evidence (including web research):\n"
        f"{_render_evidence_lines(evidence)}\n\n"
        f"{scaffold_section}"
        "Planning instructions:\n"
        "- Hard constraints and avoid directives are non-negotiable. Do not output activities that violate them.\n"
        "- If constraints conflict with candidate activities, drop or replace the conflicting activities.\n"
        "- Build coherent full days, not sparse plans. Aim for 3-5 activities per day when trip duration allows.\n"
        "- Avoid one-activity-per-day outputs unless the user explicitly asks for a very relaxed pace.\n"
        "- Keep each day internally coherent with realistic transitions and timing gaps.\n"
        "- Whenever two activities are far apart in time/location, include an explicit transport segment with from/to context.\n"
        "- If there is open time in the day, include realistic free-time or rest blocks instead of leaving long blank gaps.\n"
        "- End most days with a return-to-accommodation or evening wind-down block when possible.\n"
        "- Produce date_local (YYYY-MM-DD), start_time_local (HH:MM), and end_time_local (HH:MM) for each activity when possible.\n"
        "- Ensure duration_mins matches the difference between start_time_local and end_time_local.\n"
        "- Use web-research timing constraints when available.\n"
        "- Use realistic, activity-specific durations — do NOT round everything to 15/30/60 min:\n"
        "  Major temples/shrines: 90-180 min | Scenic hikes: 120-240 min | Full museum: 90-150 min\n"
        "  Sit-down dining: 60-90 min (never under 45 min) | Street food/café stop: 60-75 min\n"
        "  City transit leg: 20-50 min | Inter-city travel: 60-180 min | Market/shopping: 45-90 min\n"
        "  Hotel check-in/out: 30-45 min | Cultural show/performance: 60-120 min\n"
        "- If evidence or the scaffold specifies an early start (e.g. 06:00 for Fushimi Inari), use that time exactly.\n"
        "- Prefer logically coherent sequencing.\n"
        "- If a draft scaffold is provided, treat it as a starting point and improve it where needed.\n"
        "- If scaffold content conflicts with directives/evidence, correct or discard the conflicting parts.\n"
        "- If evidence conflicts, prioritize explicit user directives."
    )


def _build_scaffold_prompt(
    *,
    idea_text: str,
    trip_location: str | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str | None,
    input_directives: InputDirectives,
    evidence: list[EvidenceItem],
) -> str:
    lifestyle_section = _render_lifestyle_section(input_directives)
    lifestyle_block = f"{lifestyle_section}\n\n" if lifestyle_section else ""

    return (
        "Prepare a concise natural-language itinerary scaffold before JSON extraction.\n\n"
        "User travel request:\n"
        f"{idea_text}\n\n"
        "Trip context:\n"
        f"{_render_trip_context_section(trip_location=trip_location, start_date=start_date, end_date=end_date, timezone=timezone)}\n\n"
        f"{lifestyle_block}"
        "Planning directives:\n"
        f"{_render_directives_section(input_directives)}\n\n"
        "Evidence summary:\n"
        f"{_render_evidence_lines(evidence)}\n\n"
        "Output requirements:\n"
        "- Plain text only.\n"
        "- Keep it concise and actionable.\n"
        "- Include morning/afternoon/evening flow per day where possible.\n"
        "- Include explicit transfer/free-time/rest continuity where needed.\n"
        "- Ensure timing logic is coherent and constraints are respected.\n"
        "- If dates are given, anchor day plans to those dates."
    )


def _normalize_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = value.strip().lower()
        if len(term) < 3:
            continue
        if term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def _evidence_text_blob(item: EvidenceItem) -> str:
    return " ".join(
        [
            item.summary,
            item.source_ref,
            " ".join(item.facts.locations),
            " ".join(item.facts.activities),
            " ".join(item.facts.time_hints),
            " ".join(item.facts.constraints),
            " ".join(item.facts.vibe_tags),
            " ".join(item.citations),
        ]
    ).lower()


def _is_foundational_evidence(item: EvidenceItem) -> bool:
    return item.source_ref in {"user_prompt", "trip_context", "input_directives"}


def _score_evidence_item(item: EvidenceItem, directives: InputDirectives) -> float:
    score = item.confidence * 10.0
    if _is_foundational_evidence(item):
        score += 100.0
    if item.source_type in {"upload_image", "upload_video"}:
        score += 8.0
    if "worker_grounded" in item.id:
        score += 8.0
    if item.facts.constraints:
        score += min(len(item.facts.constraints), 3) * 3.0
    if item.facts.time_hints:
        score += min(len(item.facts.time_hints), 3) * 2.5
    if item.facts.vibe_tags:
        score += min(len(item.facts.vibe_tags), 3) * 1.5
    if item.citations:
        score += 1.5

    must_include_terms = _normalize_terms(directives.must_include)
    constraint_terms = _normalize_terms(
        directives.hard_constraints + directives.avoid
    )
    blob = _evidence_text_blob(item)
    if must_include_terms and any(term in blob for term in must_include_terms):
        score += 10.0
    if constraint_terms and any(term in blob for term in constraint_terms):
        score += 7.0
    return score


def _select_planner_evidence(
    evidence: list[EvidenceItem],
    *,
    directives: InputDirectives,
) -> list[EvidenceItem]:
    if not evidence:
        return []

    ranked: list[tuple[bool, float, int, EvidenceItem]] = []
    for index, item in enumerate(evidence):
        ranked.append(
            (
                _is_foundational_evidence(item),
                _score_evidence_item(item, directives),
                index,
                item,
            )
        )

    ranked.sort(key=lambda item: (not item[0], -item[1], item[2]))

    selected: list[EvidenceItem] = []
    selected_ids: set[str] = set()
    used_chars = 0

    for is_foundational, _, _, item in ranked:
        if item.id in selected_ids:
            continue
        line_len = len(_render_evidence_line(item)) + 1

        if len(selected) >= PLANNER_EVIDENCE_MAX_ITEMS and not is_foundational:
            continue
        if selected and used_chars + line_len > PLANNER_EVIDENCE_MAX_CHARS and not is_foundational:
            continue

        selected.append(item)
        selected_ids.add(item.id)
        used_chars += line_len

        if len(selected) >= PLANNER_EVIDENCE_MAX_ITEMS and used_chars >= PLANNER_EVIDENCE_MAX_CHARS:
            break

    if not selected:
        return evidence[:PLANNER_EVIDENCE_MAX_ITEMS]

    selected.sort(
        key=lambda item: (
            item.source_ref not in {"user_prompt", "trip_context", "input_directives"},
            evidence.index(item),
        )
    )
    return selected


def _build_media_signal_evidence(
    media_signals: list[MediaSignal],
    media_inputs: list[MediaInput],
) -> list[EvidenceItem]:
    if not media_signals:
        return []

    mime_type_by_filename = {item.filename: item.mime_type for item in media_inputs}
    evidence: list[EvidenceItem] = []
    for index, signal in enumerate(media_signals, start=1):
        filename = signal.source_filename
        mime_type = mime_type_by_filename.get(filename or "", "")
        source_type = "upload_video" if mime_type.startswith("video/") else "upload_image"
        source_ref = filename or f"media_signal_{index}"

        summary_parts = [signal.summary]
        if signal.location_cues:
            summary_parts.append(f"location cues: {_compact_values(signal.location_cues)}")
        if signal.activity_hints:
            summary_parts.append(
                f"activity hints: {_compact_values(signal.activity_hints)}"
            )

        evidence.append(
            EvidenceItem(
                id=f"ev_worker_media_ai_{index}",
                source_type=source_type,
                source_ref=source_ref,
                summary=_truncate_text(" | ".join(summary_parts), 260),
                confidence=signal.confidence,
                facts={
                    "locations": signal.location_cues,
                    "activities": signal.activity_hints,
                    "constraints": signal.constraints,
                    "vibe_tags": [*signal.vibe_tags, "vision_extracted"],
                },
                raw_artifact_ref=source_ref,
            )
        )

    return evidence


# ── Worker parallelization helpers ───────────────────────────────────────────

def _run_media_workers(
    media_inputs: list[MediaInput],
    idea_text: str,
) -> tuple[list[EvidenceItem], list[MediaSignal], str | None, list[WorkerReport]]:
    """
    Run media_context_worker followed by media_vision (sequential within media pipeline).
    Returns (evidence, signals, vision_error, reports).
    """
    reports: list[WorkerReport] = []
    all_evidence: list[EvidenceItem] = []
    signals: list[MediaSignal] = []
    vision_error: str | None = None

    if not media_inputs:
        reports.append(WorkerReport(worker_name="media_context_worker", status="SKIPPED", evidence_added=0, notes="No media uploads provided."))
        reports.append(WorkerReport(worker_name="media_vision_worker", status="SKIPPED", evidence_added=0, notes="No media uploads provided."))
        return all_evidence, signals, vision_error, reports

    media_evidence = run_media_context_worker(media_inputs)
    all_evidence.extend(media_evidence)
    reports.append(WorkerReport(worker_name="media_context_worker", status="SUCCESS", evidence_added=len(media_evidence)))

    try:
        signals = extract_media_context(idea_text=idea_text, media_inputs=media_inputs)
        media_signal_evidence = _build_media_signal_evidence(signals, media_inputs)
        if media_signal_evidence:
            all_evidence.extend(media_signal_evidence)
            reports.append(WorkerReport(
                worker_name="media_vision_worker",
                status="SUCCESS",
                evidence_added=len(media_signal_evidence),
                notes="Extracted concise visual context via OPENROUTER_IMAGE_MODEL for downstream planning.",
            ))
        else:
            reports.append(WorkerReport(
                worker_name="media_vision_worker",
                status="SKIPPED",
                evidence_added=0,
                notes="No high-confidence visual signals were extracted from uploads.",
            ))
    except Exception as exc:  # noqa: BLE001
        vision_error = str(exc)
        reports.append(WorkerReport(worker_name="media_vision_worker", status="ERROR", evidence_added=0, notes=f"Visual extraction failed: {exc}"))

    return all_evidence, signals, vision_error, reports


def _run_independent_workers(
    *,
    links: list[str],
    media_inputs: list[MediaInput],
    idea_text: str,
    input_directives: InputDirectives,
    trip_location: str | None,
) -> tuple[
    list[EvidenceItem],         # url_evidence
    list[EvidenceItem],         # media_evidence
    list[MediaSignal],          # media_signals
    str | None,                 # media_vision_error
    list[EvidenceItem],         # web_evidence
    dict[str, Any],             # web_research_debug
    list[WorkerReport],
]:
    """
    Run url, media, and web_research workers in parallel via ThreadPoolExecutor.
    web_grounding stays sequential (depends on web_research output).
    Each worker failure is isolated.
    """
    url_evidence: list[EvidenceItem] = []
    media_evidence: list[EvidenceItem] = []
    media_signals: list[MediaSignal] = []
    media_vision_error: str | None = None
    web_evidence: list[EvidenceItem] = []
    web_research_debug: dict[str, Any] = {}
    reports: list[WorkerReport] = []

    def _run_url() -> tuple[str, list[EvidenceItem]]:
        if not links:
            return "skipped", []
        logger.info("Worker input -> url_context_worker: links_count=%s links=%s", len(links), links[:WORKER_INPUT_LOG_MAX_ITEMS])
        return "success", run_url_context_worker(links)

    def _run_media() -> tuple[list[EvidenceItem], list[MediaSignal], str | None, list[WorkerReport]]:
        logger.info(
            "Worker input -> media_context_worker: media_count=%s",
            len(media_inputs),
        )
        return _run_media_workers(media_inputs, idea_text)

    def _run_web() -> Any:
        logger.info(
            "Worker input -> web_research_worker: idea_preview=%s location=%s",
            _preview_for_log(idea_text),
            trip_location,
        )
        return run_web_research_worker(
            idea_text=idea_text,
            input_directives=input_directives,
            trip_location=trip_location,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_run_url): "url",
            pool.submit(_run_media): "media",
            pool.submit(_run_web): "web",
        }
        for future in as_completed(futures):
            kind = futures[future]
            try:
                result = future.result()
                if kind == "url":
                    status, ev = result
                    url_evidence = ev
                    if status == "skipped":
                        reports.append(WorkerReport(worker_name="url_context_worker", status="SKIPPED", evidence_added=0, notes="No links provided."))
                    else:
                        reports.append(WorkerReport(worker_name="url_context_worker", status="SUCCESS", evidence_added=len(ev)))
                elif kind == "media":
                    m_ev, m_signals, m_err, m_reports = result
                    media_evidence = m_ev
                    media_signals = m_signals
                    media_vision_error = m_err
                    reports.extend(m_reports)
                elif kind == "web":
                    web_evidence = result.evidence
                    web_research_debug = result.debug
                    reports.append(WorkerReport(
                        worker_name="web_research_worker",
                        status="SUCCESS",
                        evidence_added=len(web_evidence),
                        notes=(
                            f"query_source={web_research_debug.get('query_source', 'unknown')} "
                            f"queries={len(web_research_debug.get('queries_executed', []))}"
                        ),
                    ))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Parallel worker %s failed: %s", kind, exc)
                name_map = {"url": "url_context_worker", "media": "media_context_worker", "web": "web_research_worker"}
                reports.append(WorkerReport(worker_name=name_map[kind], status="ERROR", evidence_added=0, notes=str(exc)))

    return url_evidence, media_evidence, media_signals, media_vision_error, web_evidence, web_research_debug, reports


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
    media_signals_for_debug: list[MediaSignal] = []
    media_vision_error: str | None = None

    parallelism_enabled = _env_bool("WORKER_PARALLELISM_ENABLED", True)
    if parallelism_enabled:
        logger.info("Running url/media/web_research workers in parallel (WORKER_PARALLELISM_ENABLED=true)")
        (
            url_evidence,
            para_media_evidence,
            media_signals_for_debug,
            media_vision_error,
            web_evidence,
            web_research_debug,
            worker_reports,
        ) = _run_independent_workers(
            links=links,
            media_inputs=media_inputs,
            idea_text=idea_text,
            input_directives=input_directives,
            trip_location=trip_location,
        )
        merged_evidence.extend(url_evidence)
        merged_evidence.extend(para_media_evidence)
        merged_evidence.extend(web_evidence)
        reports.extend(worker_reports)
    else:
        logger.info("Running url/media/web_research workers sequentially (WORKER_PARALLELISM_ENABLED=false)")
        # Sequential fallback
        logger.info("Worker input -> url_context_worker: links_count=%s", len(links))
        if links:
            url_evidence = run_url_context_worker(links)
            merged_evidence.extend(url_evidence)
            reports.append(WorkerReport(worker_name="url_context_worker", status="SUCCESS", evidence_added=len(url_evidence)))
        else:
            reports.append(WorkerReport(worker_name="url_context_worker", status="SKIPPED", evidence_added=0, notes="No links provided."))

        if media_inputs:
            seq_media_ev, seq_signals, seq_vis_err, seq_media_reports = _run_media_workers(media_inputs, idea_text)
            merged_evidence.extend(seq_media_ev)
            media_signals_for_debug = seq_signals
            media_vision_error = seq_vis_err
            reports.extend(seq_media_reports)
        else:
            reports.append(WorkerReport(worker_name="media_context_worker", status="SKIPPED", evidence_added=0, notes="No media uploads provided."))
            reports.append(WorkerReport(worker_name="media_vision_worker", status="SKIPPED", evidence_added=0, notes="No media uploads provided."))

        logger.info("Worker input -> web_research_worker: idea_preview=%s location=%s", _preview_for_log(idea_text), trip_location)
        web_research_result = run_web_research_worker(
            idea_text=idea_text,
            input_directives=input_directives,
            trip_location=trip_location,
        )
        web_evidence = web_research_result.evidence
        web_research_debug = web_research_result.debug
        merged_evidence.extend(web_evidence)
        reports.append(WorkerReport(
            worker_name="web_research_worker",
            status="SUCCESS",
            evidence_added=len(web_evidence),
            notes=(
                f"query_source={web_research_debug.get('query_source', 'unknown')} "
                f"queries={len(web_research_debug.get('queries_executed', []))}"
            ),
        ))

    logger.info(
        "Worker input -> web_grounding_worker: citations=%s",
        [
            citation
            for item in web_evidence[:WORKER_INPUT_LOG_MAX_ITEMS]
            for citation in item.citations[:WORKER_INPUT_LOG_MAX_ITEMS]
        ],
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

    planner_evidence = _select_planner_evidence(
        merged_evidence,
        directives=input_directives,
    )
    reports.append(
        WorkerReport(
            worker_name="evidence_budget_worker",
            status="SUCCESS",
            evidence_added=len(planner_evidence),
            notes=(
                f"Planner context reduced from {len(merged_evidence)} to "
                f"{len(planner_evidence)} evidence items."
            ),
        )
    )

    scaffold_enabled = _env_bool("PLANNER_SCAFFOLD_ENABLED", True)
    planner_scaffold_prompt: str | None = None
    planner_scaffold_text: str | None = None
    planner_scaffold_debug: dict[str, Any] = {
        "scaffold_enabled": scaffold_enabled,
    }

    if scaffold_enabled:
        planner_scaffold_prompt = _build_scaffold_prompt(
            idea_text=idea_text,
            trip_location=trip_location,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
            input_directives=input_directives,
            evidence=planner_evidence,
        )
        try:
            planner_scaffold_text, planner_scaffold_debug = build_planning_scaffold(
                scaffold_prompt=planner_scaffold_prompt
            )
            if planner_scaffold_text:
                reports.append(
                    WorkerReport(
                        worker_name="planner_scaffold_worker",
                        status="SUCCESS",
                        evidence_added=0,
                        notes=(
                            "Generated natural-language scaffold to guide JSON itinerary extraction."
                        ),
                    )
                )
            else:
                reports.append(
                    WorkerReport(
                        worker_name="planner_scaffold_worker",
                        status="SKIPPED",
                        evidence_added=0,
                        notes=(
                            "Scaffold model returned empty output; continuing with direct JSON extraction."
                        ),
                    )
                )
        except Exception as exc:  # noqa: BLE001
            planner_scaffold_debug = {
                "scaffold_enabled": True,
                "error": str(exc),
            }
            reports.append(
                WorkerReport(
                    worker_name="planner_scaffold_worker",
                    status="ERROR",
                    evidence_added=0,
                    notes=f"Scaffold generation failed: {exc}",
                )
            )
    else:
        planner_scaffold_debug = {
            "scaffold_enabled": False,
            "reason": "PLANNER_SCAFFOLD_ENABLED=false",
        }
        reports.append(
            WorkerReport(
                worker_name="planner_scaffold_worker",
                status="SKIPPED",
                evidence_added=0,
                notes="Disabled via PLANNER_SCAFFOLD_ENABLED.",
            )
        )

    # ── Critique / Revise loop ────────────────────────────────────────────────
    critique_enabled = _env_bool("PLANNER_CRITIQUE_ENABLED", True)
    planner_critique_text: str | None = None
    planner_revised_scaffold_text: str | None = None
    # The scaffold used for extraction (may be swapped for revised version)
    active_scaffold_text = planner_scaffold_text

    if critique_enabled and planner_scaffold_text:
        try:
            critique_text, _critique_debug = critique_planning_scaffold(
                scaffold_text=planner_scaffold_text,
                idea_text=idea_text,
                input_directives=input_directives,
                start_date=start_date,
                end_date=end_date,
            )
            if critique_text:
                planner_critique_text = critique_text
                reports.append(
                    WorkerReport(
                        worker_name="planner_critique_worker",
                        status="SUCCESS",
                        evidence_added=0,
                        notes=f"critique_chars={len(critique_text)}",
                    )
                )
                needs_revision = "verdict: needs_revision" in critique_text.lower()
                if needs_revision:
                    try:
                        revised_text, _revise_debug = revise_planning_scaffold(
                            original_scaffold=planner_scaffold_text,
                            critique_text=critique_text,
                            idea_text=idea_text,
                            input_directives=input_directives,
                            start_date=start_date,
                            end_date=end_date,
                        )
                        if revised_text:
                            planner_revised_scaffold_text = revised_text
                            active_scaffold_text = revised_text
                            reports.append(
                                WorkerReport(
                                    worker_name="planner_revise_worker",
                                    status="SUCCESS",
                                    evidence_added=0,
                                    notes=f"revised_chars={len(revised_text)}",
                                )
                            )
                        else:
                            reports.append(
                                WorkerReport(
                                    worker_name="planner_revise_worker",
                                    status="SKIPPED",
                                    evidence_added=0,
                                    notes="Revise model returned empty; using original scaffold.",
                                )
                            )
                    except Exception as exc:  # noqa: BLE001
                        reports.append(
                            WorkerReport(
                                worker_name="planner_revise_worker",
                                status="ERROR",
                                evidence_added=0,
                                notes=f"Revision failed (using original): {exc}",
                            )
                        )
                else:
                    reports.append(
                        WorkerReport(
                            worker_name="planner_revise_worker",
                            status="SKIPPED",
                            evidence_added=0,
                            notes="Critique verdict: approved — no revision needed.",
                        )
                    )
            else:
                reports.append(
                    WorkerReport(
                        worker_name="planner_critique_worker",
                        status="SKIPPED",
                        evidence_added=0,
                        notes="Critique model returned empty output.",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            reports.append(
                WorkerReport(
                    worker_name="planner_critique_worker",
                    status="ERROR",
                    evidence_added=0,
                    notes=f"Critique failed: {exc}",
                )
            )
    else:
        reason = "No scaffold to critique." if not planner_scaffold_text else "Disabled via PLANNER_CRITIQUE_ENABLED."
        reports.append(
            WorkerReport(
                worker_name="planner_critique_worker",
                status="SKIPPED",
                evidence_added=0,
                notes=reason,
            )
        )

    planner_prompt = _build_planner_prompt(
        idea_text=idea_text,
        trip_location=trip_location,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        input_directives=input_directives,
        evidence=planner_evidence,
        planning_scaffold=active_scaffold_text,
    )
    planner_nodes = extract_itinerary(planner_prompt)
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

    planner_evidence_lines = [_render_evidence_line(item) for item in planner_evidence]
    debug_trace: dict[str, Any] = {
        "qwen_media_signals": [asdict(signal) for signal in media_signals_for_debug],
        "qwen_media_signal_count": len(media_signals_for_debug),
        "qwen_media_error": media_vision_error,
        "planner_evidence_selected_ids": [item.id for item in planner_evidence],
        "planner_evidence_selected": [
            item.model_dump(mode="json") for item in planner_evidence
        ],
        "planner_evidence_lines": planner_evidence_lines,
        "planner_evidence_budget": {
            "max_items": PLANNER_EVIDENCE_MAX_ITEMS,
            "max_chars": PLANNER_EVIDENCE_MAX_CHARS,
            "line_max_chars": PLANNER_EVIDENCE_LINE_MAX_CHARS,
            "selected_items": len(planner_evidence),
            "selected_chars": sum(len(line) + 1 for line in planner_evidence_lines),
            "total_available_items": len(merged_evidence),
        },
        "planner_prompt_chars": len(planner_prompt),
        "planner_prompt_includes_scaffold": bool(active_scaffold_text),
        "planner_scaffold_prompt_chars": len(planner_scaffold_prompt or ""),
        "planner_scaffold_text": planner_scaffold_text,
        "planner_critique_text": planner_critique_text,
        "planner_revised_scaffold_text": planner_revised_scaffold_text,
        "planner_scaffold_debug": planner_scaffold_debug,
        "web_query_builder": web_research_debug,
    }

    return OrchestrationResult(
        nodes=nodes,
        evidence=merged_evidence,
        worker_reports=reports,
        validation_report=plan_validation_as_dict(validation_result),
        planner_scaffold_text=planner_scaffold_text,
        planner_critique_text=planner_critique_text,
        planner_revised_scaffold_text=planner_revised_scaffold_text,
        debug_trace=debug_trace,
    )


def worker_reports_as_dicts(worker_reports: list[WorkerReport]) -> list[dict]:
    return [asdict(report) for report in worker_reports]
