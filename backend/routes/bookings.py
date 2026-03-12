"""
NovaSync — /api/bookings routes for Nova Act restaurant booking.

Session-based HITL flow:
  POST   /api/bookings/start             → create session, spawn agent thread
  GET    /api/bookings/{session_id}/stream → SSE drain
  POST   /api/bookings/{session_id}/resume → unblock agent with sensitive data
  DELETE /api/bookings/{session_id}        → cancel and cleanup
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue as _queue
import threading as _threading
import time
from typing import AsyncGenerator
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from models import BookingStartRequest, BookingResumeRequest
from services.nova_booking_agent import run_booking_agent, _SENTINEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bookings", tags=["bookings"])

# ── In-memory session store ───────────────────────────────────────────────────
_BOOKING_TTL_SECONDS = 10 * 60  # 10 minutes

booking_sessions: dict[str, dict] = {}
_booking_sessions_lock = asyncio.Lock()


async def _evict_expired() -> None:
    async with _booking_sessions_lock:
        now = time.time()
        expired = [sid for sid, s in list(booking_sessions.items()) if now - s["created_at"] > _BOOKING_TTL_SECONDS]
        for sid in expired:
            booking_sessions[sid]["cancel_event"].set()
            del booking_sessions[sid]


# ── POST /api/bookings/start ─────────────────────────────────────────────────

@router.post("/start")
async def start_booking(request: BookingStartRequest):
    """Create a booking session and spawn the Nova Act agent thread."""
    await _evict_expired()

    session_id = str(uuid4())
    event_queue: _queue.Queue = _queue.Queue()
    resume_event = _threading.Event()
    resume_data_slot: list = [None]
    cancel_event = _threading.Event()

    thread = _threading.Thread(
        target=run_booking_agent,
        args=(
            request.restaurant_name,
            request.city,
            request.date,
            request.time,
            request.party_size,
            event_queue,
            resume_event,
            resume_data_slot,
            cancel_event,
        ),
        daemon=True,
    )
    thread.start()

    async with _booking_sessions_lock:
        booking_sessions[session_id] = {
            "thread": thread,
            "event_queue": event_queue,
            "resume_event": resume_event,
            "resume_data_slot": resume_data_slot,
            "cancel_event": cancel_event,
            "created_at": time.time(),
        }

    logger.info("Booking session %s started for %s in %s", session_id, request.restaurant_name, request.city)
    return {"session_id": session_id}


# ── GET /api/bookings/{session_id}/stream ─────────────────────────────────────

@router.get("/{session_id}/stream")
async def stream_booking(session_id: str):
    """SSE stream of booking agent events."""
    await _evict_expired()

    async with _booking_sessions_lock:
        session = booking_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Booking session not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        event_queue: _queue.Queue = session["event_queue"]
        loop = asyncio.get_running_loop()

        # Send connection confirmation
        yield f"event: connected\ndata: {json.dumps({'session_id': session_id})}\n\n"

        while True:
            try:
                item = await loop.run_in_executor(
                    None, lambda: event_queue.get(timeout=1.0)
                )
            except _queue.Empty:
                # Send keepalive comment
                yield ": keepalive\n\n"
                continue

            if item is _SENTINEL:
                # Agent has finished — clean up session
                async with _booking_sessions_lock:
                    booking_sessions.pop(session_id, None)
                break

            event_type = item.get("event", "message")
            data = item.get("data", {})
            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── POST /api/bookings/{session_id}/resume ────────────────────────────────────

@router.post("/{session_id}/resume")
async def resume_booking(session_id: str, request: BookingResumeRequest):
    """Unblock the paused Nova Act agent with user-supplied sensitive data."""
    async with _booking_sessions_lock:
        session = booking_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Booking session not found")

    # Store resume data (sensitive — never logged)
    session["resume_data_slot"][0] = {
        "phone": request.phone,
        "email": request.email,
        "password": request.password,
        "notes": request.notes,
    }
    session["resume_event"].set()

    return {"ok": True}


# ── DELETE /api/bookings/{session_id} ─────────────────────────────────────────

@router.delete("/{session_id}")
async def cancel_booking(session_id: str):
    """Cancel a booking session and clean up."""
    async with _booking_sessions_lock:
        session = booking_sessions.pop(session_id, None)
    if session:
        session["cancel_event"].set()
        logger.info("Booking session %s cancelled", session_id)

    return {"ok": True}
