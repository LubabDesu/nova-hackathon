"""
NovaSync — Group trip plan SSE endpoint.

Orchestrates parallel per-traveler preference extraction, consensus
building, and scaffold generation for group trips. The existing
orchestrate_scaffold() is called identically to the individual flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue as _queue
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

import threading as _threading

from fastapi import Body
from pydantic import BaseModel

from models import ConsensusResult, InputDirectives, TravelerProfile
from services.auth import get_current_user
from services.supabase_admin import get_admin_client
from services.orchestrator import orchestrate_scaffold, worker_reports_as_dicts
from services.session_cache import PlanSession, put_session
from services.evidence_builder import build_initial_evidence
from services.workers.preference_extraction_worker import extract_traveler_profile
from services.consensus_builder import build_consensus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/group-trips", tags=["group-plan"])

_active_group_questions: dict[str, tuple[_threading.Event, list[str | None]]] = {}

ORCHESTRATION_TIMEOUT_SECONDS = float(
    os.getenv("ORCHESTRATION_TIMEOUT_SECONDS", "360")
)


def _sse(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.post("/{group_id}/plan")
async def plan_group_trip(
    group_id: str,
    user: dict = Depends(get_current_user),
):
    """
    SSE endpoint that drives the group planning pipeline:
    1. Load group preferences from DB
    2. ThreadPoolExecutor: extract_traveler_profile() per traveler in parallel
    3. build_consensus() → ConsensusResult
    4. Emit consensus_ready (conflicts + participants)
    5. orchestrate_scaffold() — unchanged call to existing pipeline
    6. Cache PlanSession
    7. Emit scaffold_ready
    """

    async def stream() -> AsyncGenerator[str, None]:
        try:
            admin = get_admin_client()

            # Load trip metadata
            trip_result = (
                admin.table("trips")
                .select("id, name, trip_location, start_date, end_date, trip_days")
                .eq("id", group_id)
                .maybe_single()
                .execute()
            )
            if not trip_result.data:
                yield _sse("error", {"status_code": 404, "message": "Trip not found"})
                return
            trip = trip_result.data

            # Load group preferences
            prefs_result = (
                admin.table("group_preferences")
                .select("nickname, free_text, input_directives")
                .eq("group_id", group_id)
                .execute()
            )
            rows = prefs_result.data or []
            if not rows:
                yield _sse(
                    "error",
                    {"status_code": 400, "message": "No travelers have submitted preferences yet"},
                )
                return

            yield _sse("accepted", {
                "group_id": group_id,
                "traveler_count": len(rows),
                "traveler_names": [row["nickname"] for row in rows],
            })

            # ── Parallel per-traveler extraction ──────────────────────────────
            yield _sse(
                "stage_start",
                {"stage": "extract_preferences", "label": "Extracting traveler preferences"},
            )

            def _extract_row(row: dict) -> TravelerProfile:
                raw_directives = row.get("input_directives") or {}
                try:
                    directives = InputDirectives.model_validate(raw_directives)
                except Exception:  # noqa: BLE001
                    directives = InputDirectives()
                return extract_traveler_profile(
                    nickname=row["nickname"],
                    input_directives=directives,
                    free_text=row.get("free_text") or "",
                    trip_location=trip.get("trip_location"),
                )

            profiles: list[TravelerProfile] = []
            max_workers = min(len(rows), 6)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_extract_row, row): row["nickname"]
                    for row in rows
                }
                for future in as_completed(futures):
                    profile = future.result()
                    profiles.append(profile)
                    yield _sse(
                        "traveler_extracted",
                        {
                            "nickname": futures[future],
                            "extracted": len(profiles),
                            "total": len(rows),
                        },
                    )

            yield _sse(
                "stage_done",
                {"stage": "extract_preferences", "traveler_count": len(profiles)},
            )

            # ── Consensus ─────────────────────────────────────────────────────
            yield _sse("stage_start", {"stage": "build_consensus", "label": "Building consensus"})
            consensus: ConsensusResult = build_consensus(profiles)
            yield _sse("stage_done", {"stage": "build_consensus"})

            yield _sse(
                "consensus_ready",
                {
                    "conflicts": [c.model_dump() for c in consensus.conflicts],
                    "participants": [p.nickname for p in consensus.traveler_profiles],
                },
            )

            # ── Resolve dates ─────────────────────────────────────────────────
            start_date_val: date | None = None
            end_date_val: date | None = None
            raw_start = trip.get("start_date")
            raw_end = trip.get("end_date")
            if raw_start:
                try:
                    start_date_val = date.fromisoformat(str(raw_start))
                except ValueError:
                    pass
            if raw_end:
                try:
                    end_date_val = date.fromisoformat(str(raw_end))
                except ValueError:
                    pass
            if start_date_val is None:
                trip_days = trip.get("trip_days") or 5
                start_date_val = date.today()
                end_date_val = start_date_val + timedelta(days=max(1, int(trip_days)) - 1)

            # ── Build idea text ───────────────────────────────────────────────
            nicknames = [p.nickname for p in consensus.traveler_profiles]
            n_travelers = len(nicknames)
            dest = trip.get("trip_location") or trip.get("name") or "the destination"
            idea_text = (
                f"Group trip to {dest} for {n_travelers} "
                f"traveler{'s' if n_travelers != 1 else ''}: {', '.join(nicknames)}"
            )

            # ── Build initial evidence ────────────────────────────────────────
            inspiration_links = list(getattr(consensus.merged_directives, "inspiration_links", None) or [])
            initial_evidence = build_initial_evidence(
                idea_text=idea_text,
                trip_location=trip.get("trip_location"),
                start_date=start_date_val,
                end_date=end_date_val,
                timezone=None,
                links=inspiration_links,
                media_inputs=[],
                input_directives=consensus.merged_directives,
            )

            # ── orchestrate_scaffold — agentic loop with real-time actions ────
            yield _sse(
                "stage_start",
                {"stage": "build_scaffold", "label": "Agent is researching your group trip"},
            )
            scaffold_started = time.perf_counter()

            action_queue_g: _queue.Queue = _queue.Queue()
            question_event_g = _threading.Event()
            question_answer_g: list[str | None] = [None]
            _active_group_questions[group_id] = (question_event_g, question_answer_g)
            _loop_g = asyncio.get_event_loop()
            scaffold_future_g = _loop_g.run_in_executor(
                None,
                lambda: orchestrate_scaffold(
                    idea_text=idea_text,
                    trip_location=trip.get("trip_location"),
                    start_date=start_date_val,
                    end_date=end_date_val,
                    timezone=None,
                    links=inspiration_links,
                    media_inputs=[],
                    input_directives=consensus.merged_directives,
                    initial_evidence=initial_evidence,
                    action_queue=action_queue_g,
                    question_event=question_event_g,
                    question_answer=question_answer_g,
                ),
            )

            while not scaffold_future_g.done():
                drained = 0
                while not action_queue_g.empty() and drained < 10:
                    action = action_queue_g.get_nowait()
                    if action.get("type") == "question":
                        yield _sse("agent_question", {
                            "request_id": group_id,
                            "question_id": action["question_id"],
                            "question": action["question"],
                            "options": action["options"],
                        })
                    else:
                        yield _sse("agent_action", {
                            "tool_name": action["tool_name"],
                            "summary": action["summary"],
                            "result_preview": action.get("result_preview", ""),
                            "reasoning": action.get("reasoning", ""),
                            "scratchpad": action.get("scratchpad", ""),
                        })
                    drained += 1
                await asyncio.sleep(0.15)

            while not action_queue_g.empty():
                action = action_queue_g.get_nowait()
                if action.get("type") == "question":
                    yield _sse("agent_question", {
                        "request_id": group_id,
                        "question_id": action["question_id"],
                        "question": action["question"],
                        "options": action["options"],
                    })
                else:
                    yield _sse("agent_action", {
                        "tool_name": action["tool_name"],
                        "summary": action["summary"],
                        "result_preview": action.get("result_preview", ""),
                        "reasoning": action.get("reasoning", ""),
                        "scratchpad": action.get("scratchpad", ""),
                    })

            _active_group_questions.pop(group_id, None)
            scaffold_result = await scaffold_future_g

            yield _sse("stage_done", {"stage": "build_scaffold"})

            # ── Cache session (same as individual flow) ───────────────────────
            session_id = str(uuid4())
            put_session(PlanSession(
                session_id=session_id,
                evidence=scaffold_result.evidence,
                request_snapshot={
                    "trip_location": trip.get("trip_location"),
                    "start_date": str(start_date_val) if start_date_val else None,
                    "end_date": str(end_date_val) if end_date_val else None,
                    "timezone": None,
                    "input_directives": consensus.merged_directives.model_dump(mode="json"),
                },
                scaffold_text=scaffold_result.scaffold_text,
                idea_text=idea_text,
                revision_count=0,
            ))

            yield _sse(
                "scaffold_ready",
                {
                    "session_id": session_id,
                    "scaffold_text": scaffold_result.scaffold_text,
                    "revision_count": 0,
                    "max_revisions": 1,
                    "worker_reports": worker_reports_as_dicts(scaffold_result.worker_reports),
                    "scratchpad": getattr(scaffold_result, "agent_scratchpad", ""),
                },
            )
            yield _sse("done", {"group_id": group_id})

        except asyncio.TimeoutError:
            yield _sse("error", {"status_code": 504, "message": "Planning timed out"})
        except HTTPException as exc:
            yield _sse("error", {"status_code": exc.status_code, "message": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("group_plan SSE failed for group %s", group_id)
            yield _sse("error", {"status_code": 500, "message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class AnswerGroupQuestionRequest(BaseModel):
    group_id: str
    question_id: str
    answer: str


@router.post("/answer-question")
async def group_answer_question(
    body: AnswerGroupQuestionRequest,
    user: dict = Depends(get_current_user),
):
    """Unblock a waiting ask_user tool call for a group trip."""
    entry = _active_group_questions.get(body.group_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Question session not found or expired")
    event, container = entry
    container[0] = body.answer
    event.set()
    return {"ok": True}
