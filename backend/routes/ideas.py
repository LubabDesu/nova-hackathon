"""
NovaSync — /api routes for processing travel ideas.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from models import InputDirectives, ProcessIdeaRequest, ProcessIdeaResponse
from services.openrouter import MediaInput
from services.orchestrator import (
    orchestrate_itinerary_planning,
    worker_reports_as_dicts,
)
from services.supabase_client import create_trip, insert_nodes
from services.evidence_builder import build_initial_evidence

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ideas"])

SUPPORTED_MEDIA_PREFIXES = ("image/", "video/")
MAX_UPLOAD_FILES = 6
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024
MIME_NORMALIZATION = {
    "video/quicktime": "video/mov",
    "video/x-m4v": "video/mp4",
}


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


@router.post("/process-idea", response_model=ProcessIdeaResponse)
async def process_idea(
    request: Request,
    debug: bool = False,
    idea: str | None = Form(default=None),
    trip_id: str | None = Form(default=None),
    trip_location: str | None = Form(default=None),
    start_date: str | None = Form(default=None),
    end_date: str | None = Form(default=None),
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
            trip = create_trip(name="Untitled Trip")
            trip_id = trip["id"]

        # 2. Run deterministic orchestration + planner
        orchestration = orchestrate_itinerary_planning(
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
        insert_nodes(trip_id, nodes)

        response_payload = ProcessIdeaResponse(trip_id=trip_id, nodes=nodes)
        if debug:
            return JSONResponse(
                content={
                    **response_payload.model_dump(mode="json"),
                    "worker_reports": worker_reports_as_dicts(orchestration.worker_reports),
                    "validation_report": orchestration.validation_report,
                    "evidence": [
                        item.model_dump(mode="json") for item in orchestration.evidence
                    ],
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
