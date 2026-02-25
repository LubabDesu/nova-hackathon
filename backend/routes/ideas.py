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
    InputDirectives,
    ItineraryNode,
    ProcessIdeaRequest,
    ProcessIdeaResponse,
)
from services.openrouter import MediaInput
from services.orchestrator import (
    orchestrate_itinerary_planning,
    worker_reports_as_dicts,
)
from services.workers.url_worker import run_url_context_worker
from services.supabase_client import create_trip, insert_nodes
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
        await _run_blocking_stage(
            stage_name="insert_nodes",
            timeout_seconds=DB_TIMEOUT_SECONDS,
            fn=insert_nodes,
            trip_id=trip_id,
            nodes=nodes,
        )

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
            await _run_blocking_stage(
                stage_name="insert_nodes",
                timeout_seconds=DB_TIMEOUT_SECONDS,
                fn=insert_nodes,
                trip_id=trip_id_value,
                nodes=nodes,
            )
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
