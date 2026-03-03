"""
NovaSync — /api routes for processing travel ideas.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import time
from typing import Any, AsyncGenerator
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from models import (
    BulkUpdateNodesRequest,
    ExtractRequest,
    InputDirectives,
    ItineraryNode,
    ProcessIdeaRequest,
    ProcessIdeaResponse,
    ReoptimizeTimingsRequest,
    ReviseRequest,
)
from services.openrouter import MediaInput, revise_scaffold_with_feedback
from services.orchestrator import (
    ScaffoldResult,
    orchestrate_extraction,
    orchestrate_itinerary_planning,
    orchestrate_scaffold,
    worker_reports_as_dicts,
)
from services.session_cache import PlanSession, delete_session, get_session, put_session
from services.workers.url_worker import run_url_context_worker
from services.supabase_client import create_trip, insert_nodes, update_nodes
from services.evidence_builder import build_initial_evidence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ideas"])

ORCHESTRATION_TIMEOUT_SECONDS = float(
    os.getenv("ORCHESTRATION_TIMEOUT_SECONDS", "360")
)
DB_TIMEOUT_SECONDS = float(os.getenv("DB_TIMEOUT_SECONDS", "30"))

SUPPORTED_MEDIA_PREFIXES = ("image/", "video/")
MAX_UPLOAD_FILES = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024
MIME_NORMALIZATION = {
    "video/quicktime": "video/mov",
    "video/x-m4v": "video/mp4",
}


class UrlScraperDebugRequest(BaseModel):
    links: list[str]


async def _run_blocking_stage(
    stage_name: str,
    timeout_seconds: float,
    fn,
    *args,
    **kwargs,
):
    started_at = time.perf_counter()
    logger.info(
        "Stage start: %s (timeout=%ss)",
        stage_name,
        int(timeout_seconds),
    )
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        elapsed = time.perf_counter() - started_at
        logger.error(
            "Stage timeout: %s after %.2fs (limit=%ss)",
            stage_name,
            elapsed,
            int(timeout_seconds),
        )
        raise HTTPException(
            status_code=504,
            detail=f"{stage_name} timed out after {int(timeout_seconds)}s.",
        ) from exc

    elapsed = time.perf_counter() - started_at
    logger.info("Stage done: %s in %.2fs", stage_name, elapsed)
    return result


def _sse_event(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


def _node_sort_key(node: ItineraryNode) -> tuple[str, str, str]:
    date_key = node.date_local or "9999-12-31"
    time_key = node.start_time_local or "99:99"
    title_key = node.title.lower()
    return (date_key, time_key, title_key)


def _build_node_day_batches(nodes: list[ItineraryNode]) -> list[dict[str, Any]]:
    if not nodes:
        return []

    by_day: dict[str, list[ItineraryNode]] = {}
    for node in nodes:
        day_key = node.date_local or "unscheduled"
        by_day.setdefault(day_key, []).append(node)

    day_keys = sorted(
        by_day.keys(),
        key=lambda key: ("9999-12-31" if key == "unscheduled" else key),
    )

    batches: list[dict[str, Any]] = []
    for day_key in day_keys:
        day_nodes = sorted(by_day[day_key], key=_node_sort_key)
        batches.append(
            {
                "day": day_key,
                "nodes": [node.model_dump(mode="json") for node in day_nodes],
            }
        )
    return batches


def _normalize_links(raw_links: list[str]) -> list[str]:
    deduped_ordered: list[str] = []
    seen: set[str] = set()

    for raw in raw_links:
        candidate = raw.strip()
        if not candidate:
            continue

        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid link URL: '{candidate}'",
            )

        if candidate not in seen:
            seen.add(candidate)
            deduped_ordered.append(candidate)

    return deduped_ordered


def _parse_links_form_field(raw_links: str | None) -> list[str]:
    if not raw_links or not raw_links.strip():
        return []

    try:
        parsed = json.loads(raw_links)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="`links` must be a JSON stringified array.",
        ) from exc

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise HTTPException(
            status_code=422,
            detail="`links` must be a JSON array of strings.",
        )

    return _normalize_links(parsed)


def _parse_input_directives_form_field(raw_directives: str | None) -> InputDirectives:
    if not raw_directives or not raw_directives.strip():
        return InputDirectives()

    try:
        parsed = json.loads(raw_directives)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail="`input_directives` must be a JSON stringified object.",
        ) from exc

    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=422,
            detail="`input_directives` must be a JSON object.",
        )

    try:
        return InputDirectives.model_validate(parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _resolve_mime_type(upload: UploadFile) -> str:
    if upload.content_type and upload.content_type != "application/octet-stream":
        return MIME_NORMALIZATION.get(upload.content_type, upload.content_type)

    guessed, _ = mimetypes.guess_type(upload.filename or "")
    resolved = guessed or "application/octet-stream"
    return MIME_NORMALIZATION.get(resolved, resolved)


async def _prepare_media_inputs(files: list[UploadFile]) -> list[MediaInput]:
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum allowed is {MAX_UPLOAD_FILES}.",
        )

    prepared: list[MediaInput] = []
    for upload in files:
        filename = upload.filename or "upload"
        mime_type = _resolve_mime_type(upload)

        if not mime_type.startswith(SUPPORTED_MEDIA_PREFIXES):
            await upload.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported file type for '{filename}'. "
                    "Only image/* and video/* uploads are supported."
                ),
            )

        raw = await upload.read()
        await upload.close()

        if not raw:
            continue

        max_size = MAX_VIDEO_BYTES if mime_type.startswith("video/") else MAX_IMAGE_BYTES
        if len(raw) > max_size:
            limit_mb = max_size // (1024 * 1024)
            kind = "video" if mime_type.startswith("video/") else "image"
            raise HTTPException(
                status_code=400,
                detail=f"'{filename}' exceeds the {limit_mb}MB {kind} upload limit.",
            )

        encoded = base64.b64encode(raw).decode("ascii")
        prepared.append(
            MediaInput(
                filename=filename,
                mime_type=mime_type,
                data_url=f"data:{mime_type};base64,{encoded}",
            )
        )

    return prepared


@router.post("/debug/url-scraper")
async def debug_url_scraper(payload: UrlScraperDebugRequest):
    normalized_links = _normalize_links(payload.links)
    if not normalized_links:
        raise HTTPException(status_code=422, detail="Provide at least one valid HTTP/HTTPS link.")

    started_at = time.perf_counter()
    evidence = await _run_blocking_stage(
        stage_name="debug_url_scraper_worker",
        timeout_seconds=DB_TIMEOUT_SECONDS,
        fn=run_url_context_worker,
        links=normalized_links,
        include_full_text=True,
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0

    return {
        "links": normalized_links,
        "evidence_count": len(evidence),
        "elapsed_ms": round(elapsed_ms, 2),
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }


@router.post("/process-idea", response_model=ProcessIdeaResponse)
async def process_idea(
    request: Request,
    debug: bool = False,
    idea: str | None = Form(default=None),
    trip_id: str | None = Form(default=None),
    trip_location: str | None = Form(default=None),
    start_date: str | None = Form(default=None),
    end_date: str | None = Form(default=None),
    trip_window_mode: str | None = Form(default=None),
    trip_days: int | None = Form(default=None),
    timezone: str | None = Form(default=None),
    links: str | None = Form(default=None),
    input_directives: str | None = Form(default=None),
    files: list[UploadFile] | None = File(default=None),
):
    """
    Accept raw travel ideas + optional media, extract structured itinerary,
    persist to Supabase, and return the result.
    """
    try:
        content_type = request.headers.get("content-type", "")
        uploaded_files = files or []

        if "application/json" in content_type:
            body = ProcessIdeaRequest.model_validate(await request.json())
        else:
            body = ProcessIdeaRequest(
                idea=(idea or "").strip(),
                trip_id=trip_id,
                trip_location=(trip_location or "").strip() or None,
                start_date=start_date,
                end_date=end_date,
                trip_window_mode=(trip_window_mode or "").strip() or "fixed",
                trip_days=trip_days,
                timezone=(timezone or "").strip() or None,
                links=_parse_links_form_field(links),
                input_directives=_parse_input_directives_form_field(input_directives),
            )

        normalized_links = _normalize_links(body.links)
        body = body.model_copy(update={"links": normalized_links})
        idea_text = body.idea.strip()
        resolved_trip_id = body.trip_id

        if not idea_text:
            raise HTTPException(status_code=422, detail="`idea` must not be empty.")

        media_inputs = await _prepare_media_inputs(uploaded_files)
        evidence = build_initial_evidence(
            idea_text=idea_text,
            trip_location=body.trip_location,
            start_date=body.start_date,
            end_date=body.end_date,
            timezone=body.timezone,
            links=body.links,
            media_inputs=media_inputs,
            input_directives=body.input_directives,
        )
        logger.info("Parsed request body:\n%s", body.model_dump_json(indent=2))
        logger.info(
            "Initial normalized evidence:\n%s",
            json.dumps(
                [item.model_dump(mode="json") for item in evidence],
                indent=2,
            ),
        )

        # 1. Resolve or create trip
        if resolved_trip_id:
            trip_id = resolved_trip_id
        else:
            trip = await _run_blocking_stage(
                stage_name="create_trip",
                timeout_seconds=DB_TIMEOUT_SECONDS,
                fn=create_trip,
                name="Untitled Trip",
            )
            trip_id = trip["id"]

        # 2. Run deterministic orchestration + planner
        orchestration = await _run_blocking_stage(
            stage_name="orchestrate_itinerary_planning",
            timeout_seconds=ORCHESTRATION_TIMEOUT_SECONDS,
            fn=orchestrate_itinerary_planning,
            idea_text=idea_text,
            trip_location=body.trip_location,
            start_date=body.start_date,
            end_date=body.end_date,
            timezone=body.timezone,
            links=body.links,
            media_inputs=media_inputs,
            input_directives=body.input_directives,
            initial_evidence=evidence,
        )
        nodes = orchestration.nodes
        logger.info(
            "Worker reports:\n%s",
            json.dumps(
                worker_reports_as_dicts(orchestration.worker_reports),
                indent=2,
            ),
        )
        logger.info(
            "Validation report:\n%s",
            json.dumps(orchestration.validation_report, indent=2),
        )
        logger.info(
            "Final evidence after workers:\n%s",
            json.dumps(
                [item.model_dump(mode="json") for item in orchestration.evidence],
                indent=2,
            ),
        )
        logger.info(
            "Extracted %d nodes from idea using %d media file(s)",
            len(nodes),
            len(media_inputs),
        )

        # 3. Persist to Supabase
        saved_rows = await _run_blocking_stage(
            stage_name="insert_nodes",
            timeout_seconds=DB_TIMEOUT_SECONDS,
            fn=insert_nodes,
            trip_id=trip_id,
            nodes=nodes,
        )
        for node, row in zip(nodes, saved_rows):
            node.id = row["id"]

        response_payload = ProcessIdeaResponse(
            trip_id=trip_id,
            nodes=nodes,
            planner_scaffold_text=orchestration.planner_scaffold_text,
        )
        if debug:
            logger.info(
                "Planner debug trace:\n%s",
                json.dumps(orchestration.debug_trace or {}, indent=2),
            )
            return JSONResponse(
                content={
                    **response_payload.model_dump(mode="json"),
                    "worker_reports": worker_reports_as_dicts(orchestration.worker_reports),
                    "validation_report": orchestration.validation_report,
                    "evidence": [
                        item.model_dump(mode="json") for item in orchestration.evidence
                    ],
                    "debug_trace": orchestration.debug_trace or {},
                }
            )
        return response_payload

    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except Exception as exc:
        logger.exception("Failed to process idea")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/process-idea/stream")
async def process_idea_stream(
    request: Request,
    debug: bool = False,
    idea: str | None = Form(default=None),
    trip_id: str | None = Form(default=None),
    trip_location: str | None = Form(default=None),
    start_date: str | None = Form(default=None),
    end_date: str | None = Form(default=None),
    trip_window_mode: str | None = Form(default=None),
    trip_days: int | None = Form(default=None),
    timezone: str | None = Form(default=None),
    links: str | None = Form(default=None),
    input_directives: str | None = Form(default=None),
    files: list[UploadFile] | None = File(default=None),
):
    """
    Streaming variant of process_idea that emits SSE stage events and final payload.
    """

    async def stream() -> AsyncGenerator[str, None]:
        request_id = str(uuid4())[:8]
        total_started = time.perf_counter()
        stage_order = [
            "prepare_media",
            "create_trip",
            "orchestrate_itinerary_planning",
            "insert_nodes",
        ]

        def emit(event_name: str, payload: dict[str, Any]) -> str:
            return _sse_event(
                event_name,
                {
                    "request_id": request_id,
                    **payload,
                },
            )

        try:
            yield emit("accepted", {"stages": stage_order})

            content_type = request.headers.get("content-type", "")
            uploaded_files = files or []

            if "application/json" in content_type:
                body = ProcessIdeaRequest.model_validate(await request.json())
            else:
                body = ProcessIdeaRequest(
                    idea=(idea or "").strip(),
                    trip_id=trip_id,
                    trip_location=(trip_location or "").strip() or None,
                    start_date=start_date,
                    end_date=end_date,
                    trip_window_mode=(trip_window_mode or "").strip() or "fixed",
                    trip_days=trip_days,
                    timezone=(timezone or "").strip() or None,
                    links=_parse_links_form_field(links),
                    input_directives=_parse_input_directives_form_field(input_directives),
                )

            normalized_links = _normalize_links(body.links)
            body = body.model_copy(update={"links": normalized_links})
            idea_text = body.idea.strip()
            resolved_trip_id = body.trip_id

            if not idea_text:
                raise HTTPException(status_code=422, detail="`idea` must not be empty.")

            yield emit(
                "stage_start",
                {
                    "stage": "prepare_media",
                    "label": "Curating evidence",
                    "detail": "Preparing uploads, links, and directives.",
                },
            )
            media_started = time.perf_counter()
            media_inputs = await _prepare_media_inputs(uploaded_files)
            evidence = build_initial_evidence(
                idea_text=idea_text,
                trip_location=body.trip_location,
                start_date=body.start_date,
                end_date=body.end_date,
                timezone=body.timezone,
                links=body.links,
                media_inputs=media_inputs,
                input_directives=body.input_directives,
            )
            yield emit(
                "stage_done",
                {
                    "stage": "prepare_media",
                    "elapsed_ms": (time.perf_counter() - media_started) * 1000.0,
                    "evidence_count": len(evidence),
                },
            )

            if resolved_trip_id:
                trip_id_value = resolved_trip_id
                yield emit(
                    "stage_start",
                    {
                        "stage": "create_trip",
                        "label": "Using existing trip",
                        "detail": "Reusing the provided trip session.",
                    },
                )
                yield emit(
                    "stage_done",
                    {
                        "stage": "create_trip",
                        "elapsed_ms": 0,
                        "skipped": True,
                    },
                )
            else:
                yield emit(
                    "stage_start",
                    {
                        "stage": "create_trip",
                        "label": "Creating trip session",
                        "detail": "Allocating trip record before planning.",
                    },
                )
                create_started = time.perf_counter()
                trip = await _run_blocking_stage(
                    stage_name="create_trip",
                    timeout_seconds=DB_TIMEOUT_SECONDS,
                    fn=create_trip,
                    name="Untitled Trip",
                )
                trip_id_value = trip["id"]
                yield emit(
                    "stage_done",
                    {
                        "stage": "create_trip",
                        "elapsed_ms": (time.perf_counter() - create_started) * 1000.0,
                    },
                )

            yield emit(
                "stage_start",
                {
                    "stage": "orchestrate_itinerary_planning",
                    "label": "Model is thinking",
                    "detail": "Running evidence workers, visual parsing, planning, and validation.",
                },
            )
            orchestration_started = time.perf_counter()
            orchestration = await _run_blocking_stage(
                stage_name="orchestrate_itinerary_planning",
                timeout_seconds=ORCHESTRATION_TIMEOUT_SECONDS,
                fn=orchestrate_itinerary_planning,
                idea_text=idea_text,
                trip_location=body.trip_location,
                start_date=body.start_date,
                end_date=body.end_date,
                timezone=body.timezone,
                links=body.links,
                media_inputs=media_inputs,
                input_directives=body.input_directives,
                initial_evidence=evidence,
            )
            nodes = orchestration.nodes
            yield emit(
                "stage_done",
                {
                    "stage": "orchestrate_itinerary_planning",
                    "elapsed_ms": (time.perf_counter() - orchestration_started) * 1000.0,
                    "node_count": len(nodes),
                },
            )
            if orchestration.planner_scaffold_text:
                yield emit(
                    "planner_reasoning",
                    {
                        "label": "Planner draft reasoning",
                        "text": orchestration.planner_scaffold_text,
                        "chars": len(orchestration.planner_scaffold_text),
                    },
                )
            if orchestration.planner_critique_text:
                yield emit(
                    "planner_critique",
                    {
                        "label": "Self-review",
                        "text": orchestration.planner_critique_text,
                    },
                )
            if orchestration.planner_revised_scaffold_text:
                yield emit(
                    "planner_revised_reasoning",
                    {
                        "label": "Improved draft",
                        "text": orchestration.planner_revised_scaffold_text,
                    },
                )

            # node_batch events carry id=None (DB ids are populated after insert_nodes below)
            day_batches = _build_node_day_batches(nodes)
            total_batches = len(day_batches)
            streamed_node_count = 0
            for sequence, batch in enumerate(day_batches, start=1):
                batch_nodes = batch["nodes"]
                streamed_node_count += len(batch_nodes)
                yield emit(
                    "node_batch",
                    {
                        "sequence": sequence,
                        "total_batches": total_batches,
                        "day": batch["day"],
                        "batch_count": len(batch_nodes),
                        "streamed_node_count": streamed_node_count,
                        "total_nodes": len(nodes),
                        "nodes": batch_nodes,
                    },
                )

            yield emit(
                "stage_start",
                {
                    "stage": "insert_nodes",
                    "label": "Finalizing itinerary",
                    "detail": "Persisting planned nodes.",
                },
            )
            insert_started = time.perf_counter()
            saved_rows = await _run_blocking_stage(
                stage_name="insert_nodes",
                timeout_seconds=DB_TIMEOUT_SECONDS,
                fn=insert_nodes,
                trip_id=trip_id_value,
                nodes=nodes,
            )
            for node, row in zip(nodes, saved_rows):
                node.id = row["id"]
            yield emit(
                "stage_done",
                {
                    "stage": "insert_nodes",
                    "elapsed_ms": (time.perf_counter() - insert_started) * 1000.0,
                },
            )

            response_payload = ProcessIdeaResponse(
                trip_id=trip_id_value,
                nodes=nodes,
                planner_scaffold_text=orchestration.planner_scaffold_text,
            )
            final_payload: dict[str, Any] = response_payload.model_dump(mode="json")
            if debug:
                logger.info(
                    "Planner debug trace:\n%s",
                    json.dumps(orchestration.debug_trace or {}, indent=2),
                )
                final_payload |= {
                    "worker_reports": worker_reports_as_dicts(orchestration.worker_reports),
                    "validation_report": orchestration.validation_report,
                    "evidence": [
                        item.model_dump(mode="json")
                        for item in orchestration.evidence
                    ],
                    "debug_trace": orchestration.debug_trace or {},
                }
            yield emit("final", final_payload)
            yield emit(
                "done",
                {"elapsed_ms": (time.perf_counter() - total_started) * 1000.0},
            )
        except HTTPException as exc:
            yield emit(
                "error",
                {
                    "status_code": exc.status_code,
                    "message": str(exc.detail),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Streaming process_idea failed")
            yield emit(
                "error",
                {
                    "status_code": 500,
                    "message": str(exc),
                },
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Human-in-the-loop scaffold review endpoints ───────────────────────────────

@router.post("/ideas/plan")
async def ideas_plan(
    idea: str | None = Form(default=None),
    trip_location: str | None = Form(default=None),
    start_date: str | None = Form(default=None),
    end_date: str | None = Form(default=None),
    trip_window_mode: str | None = Form(default=None),
    trip_days: int | None = Form(default=None),
    timezone: str | None = Form(default=None),
    links: str | None = Form(default=None),
    input_directives: str | None = Form(default=None),
    files: list[UploadFile] | None = File(default=None),
):
    """
    Always-fresh scaffold generation endpoint (SSE).
    Runs workers + scaffold + critique/revise, caches result, emits scaffold_ready.
    Never reads an existing session_id — always creates a fresh one.
    """

    async def stream() -> AsyncGenerator[str, None]:
        request_id = str(uuid4())[:8]
        total_started = time.perf_counter()

        def emit(event_name: str, payload: dict[str, Any]) -> str:
            return _sse_event(event_name, {"request_id": request_id, **payload})

        try:
            yield emit("accepted", {"stages": ["prepare_media", "build_scaffold"]})

            uploaded_files = files or []
            idea_text = (idea or "").strip()
            if not idea_text:
                raise HTTPException(status_code=422, detail="`idea` must not be empty.")

            parsed_links = _parse_links_form_field(links)
            parsed_directives = _parse_input_directives_form_field(input_directives)
            normalized_links = _normalize_links(parsed_links)
            body = ProcessIdeaRequest(
                idea=idea_text,
                trip_location=(trip_location or "").strip() or None,
                start_date=start_date,
                end_date=end_date,
                trip_window_mode=(trip_window_mode or "").strip() or "fixed",
                trip_days=trip_days,
                timezone=(timezone or "").strip() or None,
                links=normalized_links,
                input_directives=parsed_directives,
            )

            yield emit(
                "stage_start",
                {
                    "stage": "prepare_media",
                    "label": "Curating evidence",
                    "detail": "Preparing uploads, links, and directives.",
                },
            )
            media_started = time.perf_counter()
            media_inputs = await _prepare_media_inputs(uploaded_files)
            initial_evidence = build_initial_evidence(
                idea_text=idea_text,
                trip_location=body.trip_location,
                start_date=body.start_date,
                end_date=body.end_date,
                timezone=body.timezone,
                links=body.links,
                media_inputs=media_inputs,
                input_directives=body.input_directives,
            )
            yield emit(
                "stage_done",
                {
                    "stage": "prepare_media",
                    "elapsed_ms": (time.perf_counter() - media_started) * 1000.0,
                    "evidence_count": len(initial_evidence),
                },
            )

            yield emit(
                "stage_start",
                {
                    "stage": "build_scaffold",
                    "label": "Building draft plan",
                    "detail": "Running evidence workers, web research, and scaffold generation.",
                },
            )
            scaffold_started = time.perf_counter()
            scaffold_result: ScaffoldResult = await _run_blocking_stage(
                stage_name="orchestrate_scaffold",
                timeout_seconds=ORCHESTRATION_TIMEOUT_SECONDS,
                fn=orchestrate_scaffold,
                idea_text=idea_text,
                trip_location=body.trip_location,
                start_date=body.start_date,
                end_date=body.end_date,
                timezone=body.timezone,
                links=body.links,
                media_inputs=media_inputs,
                input_directives=body.input_directives,
                initial_evidence=initial_evidence,
            )
            yield emit(
                "stage_done",
                {
                    "stage": "build_scaffold",
                    "elapsed_ms": (time.perf_counter() - scaffold_started) * 1000.0,
                },
            )

            session_id = str(uuid4())
            put_session(PlanSession(
                session_id=session_id,
                evidence=scaffold_result.evidence,
                request_snapshot={
                    "trip_location": body.trip_location,
                    "start_date": str(body.start_date) if body.start_date else None,
                    "end_date": str(body.end_date) if body.end_date else None,
                    "timezone": body.timezone,
                    "input_directives": body.input_directives.model_dump(mode="json"),
                },
                scaffold_text=scaffold_result.scaffold_text,
                idea_text=idea_text,
                revision_count=0,
            ))

            yield emit(
                "scaffold_ready",
                {
                    "session_id": session_id,
                    "scaffold_text": scaffold_result.scaffold_text,
                    "revision_count": 0,
                    "max_revisions": 1,
                },
            )
            yield emit("done", {"elapsed_ms": (time.perf_counter() - total_started) * 1000.0})

        except HTTPException as exc:
            yield emit("error", {"status_code": exc.status_code, "message": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("ideas/plan SSE failed")
            yield emit("error", {"status_code": 500, "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/ideas/revise")
async def ideas_revise(body: ReviseRequest):
    """
    User-feedback scaffold revision endpoint (plain JSON, not SSE).
    Accepts user feedback, runs a fast LLM revision, updates and returns the session.
    Returns 404 if session is missing/expired, 409 if max revisions exceeded.
    """
    session = get_session(body.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found or expired. Please start a new plan.",
        )

    if session.revision_count >= 1:
        raise HTTPException(
            status_code=409,
            detail="max_revisions_reached",
        )

    snapshot = session.request_snapshot
    revised_text, _debug = await asyncio.to_thread(
        revise_scaffold_with_feedback,
        original_scaffold=body.scaffold_text,
        user_feedback=body.user_feedback,
        idea_text=session.idea_text,
        input_directives=None,
        start_date=snapshot.get("start_date"),
        end_date=snapshot.get("end_date"),
    )

    if not revised_text:
        # Fail open: return original scaffold unchanged
        revised_text = body.scaffold_text

    session.scaffold_text = revised_text
    session.revision_count += 1
    put_session(session)

    return {"scaffold_text": revised_text, "revision_count": session.revision_count}


@router.post("/ideas/extract")
async def ideas_extract(body: ExtractRequest):
    """
    Final extraction endpoint (SSE).
    Takes approved scaffold + session_id, runs extraction, streams node batches.
    Deletes session from cache after successful extraction.
    """

    async def stream() -> AsyncGenerator[str, None]:
        request_id = str(uuid4())[:8]
        total_started = time.perf_counter()

        def emit(event_name: str, payload: dict[str, Any]) -> str:
            return _sse_event(event_name, {"request_id": request_id, **payload})

        try:
            session = get_session(body.session_id)
            if session is None:
                yield emit("error", {
                    "status_code": 404,
                    "message": "Session not found or expired. Please start a new plan.",
                })
                return

            yield emit("accepted", {"stages": ["create_trip", "extract_itinerary", "insert_nodes"]})

            snapshot = session.request_snapshot
            input_directives_data = snapshot.get("input_directives", {})
            if isinstance(input_directives_data, dict):
                from pydantic import ValidationError as _VE
                try:
                    resolved_directives = InputDirectives.model_validate(input_directives_data)
                except _VE:
                    resolved_directives = InputDirectives()
            else:
                resolved_directives = InputDirectives()

            yield emit(
                "stage_start",
                {
                    "stage": "create_trip",
                    "label": "Creating trip session",
                    "detail": "Allocating trip record before extraction.",
                },
            )
            create_started = time.perf_counter()
            trip = await _run_blocking_stage(
                stage_name="create_trip",
                timeout_seconds=DB_TIMEOUT_SECONDS,
                fn=create_trip,
                name="Untitled Trip",
            )
            trip_id_value = trip["id"]
            yield emit(
                "stage_done",
                {
                    "stage": "create_trip",
                    "elapsed_ms": (time.perf_counter() - create_started) * 1000.0,
                },
            )

            yield emit(
                "stage_start",
                {
                    "stage": "extract_itinerary",
                    "label": "Generating itinerary",
                    "detail": "Extracting structured plan from approved scaffold.",
                },
            )
            extract_started = time.perf_counter()

            from datetime import date as _date
            start_date_parsed: _date | None = None
            end_date_parsed: _date | None = None
            raw_start = snapshot.get("start_date")
            raw_end = snapshot.get("end_date")
            if raw_start:
                try:
                    start_date_parsed = _date.fromisoformat(str(raw_start))
                except ValueError:
                    pass
            if raw_end:
                try:
                    end_date_parsed = _date.fromisoformat(str(raw_end))
                except ValueError:
                    pass

            orchestration = await _run_blocking_stage(
                stage_name="orchestrate_extraction",
                timeout_seconds=ORCHESTRATION_TIMEOUT_SECONDS,
                fn=orchestrate_extraction,
                approved_scaffold=body.approved_scaffold,
                evidence=session.evidence,
                idea_text=session.idea_text,
                trip_location=snapshot.get("trip_location"),
                start_date=start_date_parsed,
                end_date=end_date_parsed,
                timezone=snapshot.get("timezone"),
                input_directives=resolved_directives,
            )
            nodes = orchestration.nodes
            yield emit(
                "stage_done",
                {
                    "stage": "extract_itinerary",
                    "elapsed_ms": (time.perf_counter() - extract_started) * 1000.0,
                    "node_count": len(nodes),
                },
            )

            # node_batch events carry id=None (DB ids are populated after insert_nodes below)
            day_batches = _build_node_day_batches(nodes)
            total_batches = len(day_batches)
            streamed_node_count = 0
            for sequence, batch in enumerate(day_batches, start=1):
                batch_nodes = batch["nodes"]
                streamed_node_count += len(batch_nodes)
                yield emit(
                    "node_batch",
                    {
                        "sequence": sequence,
                        "total_batches": total_batches,
                        "day": batch["day"],
                        "batch_count": len(batch_nodes),
                        "streamed_node_count": streamed_node_count,
                        "total_nodes": len(nodes),
                        "nodes": batch_nodes,
                    },
                )

            yield emit(
                "stage_start",
                {
                    "stage": "insert_nodes",
                    "label": "Finalizing itinerary",
                    "detail": "Persisting planned nodes.",
                },
            )
            insert_started = time.perf_counter()
            saved_rows = await _run_blocking_stage(
                stage_name="insert_nodes",
                timeout_seconds=DB_TIMEOUT_SECONDS,
                fn=insert_nodes,
                trip_id=trip_id_value,
                nodes=nodes,
            )
            for node, row in zip(nodes, saved_rows):
                node.id = row["id"]
            yield emit(
                "stage_done",
                {
                    "stage": "insert_nodes",
                    "elapsed_ms": (time.perf_counter() - insert_started) * 1000.0,
                },
            )

            delete_session(body.session_id)

            response_payload = ProcessIdeaResponse(
                trip_id=trip_id_value,
                nodes=nodes,
                planner_scaffold_text=body.approved_scaffold,
            )
            final_payload: dict[str, Any] = response_payload.model_dump(mode="json")
            if body.debug:
                logger.info(
                    "Planner debug trace:\n%s",
                    json.dumps(orchestration.debug_trace or {}, indent=2),
                )
                final_payload |= {
                    "worker_reports": worker_reports_as_dicts(orchestration.worker_reports),
                    "validation_report": orchestration.validation_report,
                    "evidence": [item.model_dump(mode="json") for item in orchestration.evidence],
                    "debug_trace": orchestration.debug_trace or {},
                }
            yield emit("final", final_payload)
            yield emit("done", {"elapsed_ms": (time.perf_counter() - total_started) * 1000.0})

        except HTTPException as exc:
            yield emit("error", {"status_code": exc.status_code, "message": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("ideas/extract SSE failed")
            yield emit("error", {"status_code": 500, "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/trips/{trip_id}/nodes")
async def bulk_update_nodes(trip_id: str, body: BulkUpdateNodesRequest):
    """Bulk-upsert edited itinerary nodes for a trip."""
    try:
        saved = await asyncio.to_thread(update_nodes, body.nodes)
        return JSONResponse({"updated": len(saved), "nodes": saved})
    except Exception as exc:
        logger.exception("bulk_update_nodes failed for trip %s", trip_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/trips/{trip_id}/reoptimize-timings")
async def reoptimize_timings(trip_id: str, body: ReoptimizeTimingsRequest):
    """Use a lightweight model to assign plausible times to activities."""
    import os, httpx, json as _json

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")

    model = os.getenv("OPENROUTER_URL_SUMMARY_MODEL", "liquid/lfm-2.5-1.2b-instruct:free")

    day_lines = []
    for day in body.days:
        acts = "; ".join(
            f"{a.get('title','?')} ({a.get('activity_type','?')}, {a.get('duration_mins',60)}min)"
            for a in day.activities
        )
        day_lines.append(f"Date {day.date}: {acts}")

    prompt = (
        f"Day starts at {body.wake_time}. Assign realistic start times to these activities.\n"
        "Rules: sightseeing/culture in morning, food at meal times (lunch 12:00, dinner 18:30), "
        "accommodation/rest in evening. No gaps between consecutive activities.\n"
        "Return ONLY valid JSON, no prose:\n"
        '{"days":[{"date":"YYYY-MM-DD","activities":[{"title":"...","start_time_local":"HH:MM","end_time_local":"HH:MM"}]}]}\n\n'
        + "\n".join(day_lines)
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OpenRouter returned {exc.response.status_code}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OpenRouter network error: {exc}",
            ) from exc
    content = resp.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]

    try:
        result = _json.loads(content)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Model returned invalid JSON: {content[:200]}") from exc

    return JSONResponse(result)
