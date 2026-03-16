"""
<<<<<<< HEAD
NovaSync — Booking endpoints.

POST /api/bookings/book
  SSE stream that runs a Nova Act browser session to find and book a
  restaurant matching the user's description, then streams logs,
  screenshots, and the final result to the client.
"""

=======
NovaSync — /api/bookings routes for Nova Act restaurant booking.

Session-based HITL flow:
  POST   /api/bookings/start             → create session, spawn agent thread
  GET    /api/bookings/{session_id}/stream → SSE drain
  POST   /api/bookings/{session_id}/resume → unblock agent with sensitive data
  DELETE /api/bookings/{session_id}        → cancel and cleanup
"""
>>>>>>> 5b82fa2e71d27a972ba37699c3f642d6766b64a8
from __future__ import annotations

import asyncio
import json
import logging
<<<<<<< HEAD
import queue
import threading
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.nova_act import JapaneseRestaurantBookingAgent, BookingResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bookings")

# Maps booking_id → threading.Event that unblocks an agent waiting for auth.
_resume_events: dict[str, threading.Event] = {}


class BookingRequest(BaseModel):
    restaurant_name: str
    restaurant_description: str | None = None
    trip_location: str | None = None
    restaurant_url: str | None = None  # accepted for backwards-compat; ignored
    date: str
    time: str
    party_size: int
    phone_number: str


def _emit(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream_booking(body: BookingRequest) -> AsyncGenerator[str, None]:
    event_queue: queue.Queue = queue.Queue()
    booking_id = str(uuid.uuid4())
    resume_event = threading.Event()
    _resume_events[booking_id] = resume_event

    agent = JapaneseRestaurantBookingAgent(
        restaurant_name=body.restaurant_name,
        restaurant_description=body.restaurant_description,
        trip_location=body.trip_location,
        date=body.date,
        time=body.time,
        party_size=body.party_size,
        phone_number=body.phone_number,
        event_queue=event_queue,
        booking_id=booking_id,
        resume_event=resume_event,
    )

    thread = threading.Thread(target=agent.run, daemon=True)
    thread.start()

    yield _emit("connected", {"status": "connected", "booking_id": booking_id})

    idle_ticks = 0
    try:
        # Drain events while the agent thread is alive.
        while thread.is_alive() or not event_queue.empty():
            drained = 0
            while not event_queue.empty() and drained < 20:
                item: dict = event_queue.get_nowait()
                drained += 1
                idle_ticks = 0

                if item.get("type") == "done":
                    return

                yield _yield_item(item)

            idle_ticks += 1
            # Emit an SSE comment keepalive every ~5 s (50 × 0.1 s) to prevent
            # proxy timeout during the 5-minute auth pause window.
            if idle_ticks % 50 == 0:
                yield ": keepalive\n\n"

            await asyncio.sleep(0.1)

        # Flush any remaining items after the thread exits.
        while not event_queue.empty():
            item = event_queue.get_nowait()
            if item.get("type") == "done":
                return
            yield _yield_item(item)
    finally:
        _resume_events.pop(booking_id, None)


def _yield_item(item: dict) -> str:
    event_type = item.get("type")

    if event_type == "log":
        return _emit("log", {
            "message": item.get("message", ""),
            "type": item.get("log_type", "info"),
        })

    if event_type == "screenshot":
        return _emit("screenshot", {"image_data": item.get("image_data", "")})

    if event_type == "result":
        result: BookingResult = item["result"]
        payload: dict = {
            "success": result.success,
            "error": result.error,
        }
        if result.success:
            payload["details"] = {
                "confirmation_number": result.confirmation_number,
                "booking_time": result.booking_time,
                "notes": result.notes,
            }
        return _emit("result", payload)

    if event_type == "step":
        return _emit("step", {
            "action": item.get("action", ""),
            "text": item.get("text", ""),
        })

    if event_type == "needs_auth":
        return _emit("needs_auth", {
            "message": item.get("message", ""),
            "auth_url": item.get("auth_url"),
        })

    if event_type == "needs_course_review":
        return _emit("needs_course_review", {
            "message": item.get("message", ""),
            "summary": item.get("summary", ""),
        })

    # Unknown event — pass through as a log entry.
    return _emit("log", {"message": str(item), "type": "info"})


@router.post("/{booking_id}/resume", status_code=200)
async def resume_booking(booking_id: str) -> dict:
    """
    Signal a paused booking agent to resume after the user has signed in.

    The booking SSE stream holds a threading.Event per session.  This endpoint
    sets that event, unblocking the agent thread which then proceeds with Phase 2.
    """
    event = _resume_events.get(booking_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Session '{booking_id}' not found")
    event.set()
    return {"status": "resumed", "booking_id": booking_id}


@router.post("/book")
async def book_restaurant(body: BookingRequest) -> StreamingResponse:
    """
    Stream a Nova Act browser session that finds and books a restaurant.

    The client receives SSE events:
      connected  — session started
      log        — agent activity log entry
      screenshot — base64 PNG of current browser state
      result     — final BookingResult (success/error + details)
    """
    return StreamingResponse(
        _stream_booking(body),
=======
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
>>>>>>> 5b82fa2e71d27a972ba37699c3f642d6766b64a8
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
<<<<<<< HEAD
=======


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
>>>>>>> 5b82fa2e71d27a972ba37699c3f642d6766b64a8
