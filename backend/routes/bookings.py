"""
NovaSync — Booking endpoints.

POST /api/bookings/book
  SSE stream that runs a Nova Act browser session to find and book a
  restaurant matching the user's description, then streams logs,
  screenshots, and the final result to the client.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
