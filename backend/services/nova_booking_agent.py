"""
NovaSync — Nova Act automated Japanese restaurant booking agent.

Runs in a background thread. Communicates via:
  - event_queue:      queue.Queue  — emits SSE events (log, screenshot, needs_user_input, result, error)
  - resume_event:     threading.Event — blocks until user supplies sensitive info
  - resume_data_slot: list[dict|None] — resume_data_slot[0] holds user-supplied data after resume_event fires
  - cancel_event:     threading.Event — checked between steps; set to abort gracefully
"""
from __future__ import annotations

import base64
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

BOOKING_TIMEOUT_SECONDS = 120
_SENTINEL = object()


def _emit(event_queue: queue.Queue, event_type: str, data: dict) -> None:
    """Put a typed SSE event onto the queue."""
    event_queue.put({"event": event_type, "data": data})


def _emit_log(event_queue: queue.Queue, message: str, log_type: str = "info") -> None:
    _emit(event_queue, "log", {"message": message, "type": log_type, "timestamp": time.strftime("%H:%M:%S")})


def _emit_screenshot(event_queue: queue.Queue, nova) -> None:
    """Take a screenshot and emit it. Silently skip if screenshot fails."""
    try:
        screenshot_bytes = nova.take_screenshot()
        if screenshot_bytes:
            image_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            _emit(event_queue, "screenshot", {"image_data": image_b64})
    except Exception as exc:
        logger.debug("Screenshot failed: %s", exc)


def _check_cancelled(cancel_event: threading.Event) -> bool:
    return cancel_event.is_set()


def run_booking_agent(
    restaurant_name: str,
    city: str,
    date: str,
    time_str: str,
    party_size: int,
    event_queue: queue.Queue,
    resume_event: threading.Event,
    resume_data_slot: list,
    cancel_event: threading.Event,
) -> None:
    """
    Main booking agent loop. Runs in a daemon thread.
    Emits SSE events onto event_queue throughout execution.
    """
    try:
        _run_booking(
            restaurant_name=restaurant_name,
            city=city,
            date=date,
            time_str=time_str,
            party_size=party_size,
            event_queue=event_queue,
            resume_event=resume_event,
            resume_data_slot=resume_data_slot,
            cancel_event=cancel_event,
        )
    except Exception as exc:
        logger.exception("Booking agent crashed: %s", exc)
        _emit(event_queue, "error", {"message": f"Agent error: {exc}"})
    finally:
        event_queue.put(_SENTINEL)


def _run_booking(
    restaurant_name: str,
    city: str,
    date: str,
    time_str: str,
    party_size: int,
    event_queue: queue.Queue,
    resume_event: threading.Event,
    resume_data_slot: list,
    cancel_event: threading.Event,
) -> None:
    """Core booking logic using Nova Act."""
    try:
        from nova_act import NovaAct
    except ImportError:
        _emit_log(event_queue, "nova_act library not installed — running in stub mode", "info")
        _run_stub_booking(restaurant_name, city, date, time_str, party_size, event_queue, resume_event, resume_data_slot, cancel_event)
        return

    _emit_log(event_queue, f"Starting booking for {restaurant_name} in {city}", "info")

    tabelog_success = _try_tabelog(
        nova_act_cls=NovaAct,
        restaurant_name=restaurant_name,
        city=city,
        date=date,
        time_str=time_str,
        party_size=party_size,
        event_queue=event_queue,
        resume_event=resume_event,
        resume_data_slot=resume_data_slot,
        cancel_event=cancel_event,
    )

    if not tabelog_success and not _check_cancelled(cancel_event):
        _emit_log(event_queue, "Tabelog failed — retrying with Google Maps", "info")
        _try_google_maps(
            nova_act_cls=NovaAct,
            restaurant_name=restaurant_name,
            city=city,
            date=date,
            time_str=time_str,
            party_size=party_size,
            event_queue=event_queue,
            resume_event=resume_event,
            resume_data_slot=resume_data_slot,
            cancel_event=cancel_event,
        )


def _try_tabelog(
    nova_act_cls,
    restaurant_name: str,
    city: str,
    date: str,
    time_str: str,
    party_size: int,
    event_queue: queue.Queue,
    resume_event: threading.Event,
    resume_data_slot: list,
    cancel_event: threading.Event,
) -> bool:
    """Attempt booking via tabelog.com. Returns True if booking was completed or attempted."""
    try:
        with nova_act_cls("https://tabelog.com") as nova:
            _emit_log(event_queue, "Opened Tabelog", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            nova.act(f"Search for {restaurant_name} in {city} in Japanese")
            _emit_log(event_queue, f"Searched for {restaurant_name}", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            nova.act("Click the most relevant restaurant result")
            _emit_log(event_queue, "Clicked restaurant result", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            nova.act("Find and click the reservation or booking button (予約する)")
            _emit_log(event_queue, "Clicked reservation button (予約する)", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            nova.act(f"Set the date to {date}, time to {time_str}, and party size to {party_size} people (人数)")
            _emit_log(event_queue, f"Set date={date}, time={time_str}, party={party_size}", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            # Request sensitive user input (phone number)
            _emit(event_queue, "needs_user_input", {"fields": ["phone"]})
            _emit_log(event_queue, "Waiting for phone number from user...", "info")

            resume_event.wait(timeout=BOOKING_TIMEOUT_SECONDS)
            if _check_cancelled(cancel_event): return False
            if not resume_event.is_set():
                _emit(event_queue, "error", {"message": "Timed out waiting for phone number"})
                return False

            user_data: dict = resume_data_slot[0] or {}
            phone = user_data.get("phone", "")
            if not phone:
                _emit(event_queue, "error", {"message": "No phone number provided"})
                return False

            # Never log phone — just confirm receipt
            _emit_log(event_queue, "Phone number received, entering details...", "info")

            # SECURITY NOTE: phone is passed to Nova Act's vision model instruction.
            # Nova Act does not log prompt strings in production mode. In development,
            # ensure NOVA_ACT_LOG_LEVEL is not set to DEBUG to prevent phone exposure.
            nova.act(f"Enter the phone number {phone} in the phone number field")
            _emit_log(event_queue, "Entered contact details", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            nova.act("Review the reservation form and submit it")
            _emit_log(event_queue, "Submitted reservation form", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return False

            # Extract confirmation
            confirmation_text = nova.act(
                "Extract the confirmation number and booking details from the page. "
                "Return the confirmation number, booking time, and any important notes."
            )
            _emit_log(event_queue, "Extracted confirmation details", "success")
            _emit_screenshot(event_queue, nova)

            confirmation_number = None
            if confirmation_text and hasattr(confirmation_text, "response"):
                confirmation_number = str(confirmation_text.response)

            _emit(event_queue, "result", {
                "success": True,
                "details": {
                    "confirmation_number": confirmation_number,
                    "booking_time": f"{date} {time_str}",
                    "notes": f"Booked {party_size} people at {restaurant_name}",
                },
            })
            return True

    except Exception as exc:
        logger.warning("Tabelog booking attempt failed: %s", exc)
        _emit_log(event_queue, f"Tabelog attempt failed: {exc}", "error")
        return False


def _try_google_maps(
    nova_act_cls,
    restaurant_name: str,
    city: str,
    date: str,
    time_str: str,
    party_size: int,
    event_queue: queue.Queue,
    resume_event: threading.Event,
    resume_data_slot: list,
    cancel_event: threading.Event,
) -> None:
    """Fallback: attempt booking via Google Maps."""
    try:
        with nova_act_cls("https://www.google.com/maps") as nova:
            _emit_log(event_queue, "Opened Google Maps as fallback", "info")
            _emit_screenshot(event_queue, nova)

            nova.act(f"Search for {restaurant_name} {city} restaurant")
            _emit_log(event_queue, "Searched on Google Maps", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return

            nova.act("Click the most relevant restaurant result")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return

            nova.act("Find and click the Reserve a table or Book a table button")
            _emit_log(event_queue, "Clicked booking button on Google Maps", "action")
            _emit_screenshot(event_queue, nova)
            if _check_cancelled(cancel_event): return

            # Ask user if they need to complete manually
            _emit(event_queue, "needs_user_input", {"fields": ["manual_required"]})
            _emit_log(event_queue, "Please complete booking manually if needed", "info")
            _emit(event_queue, "result", {
                "success": False,
                "error": "Automatic booking could not be completed. Please finish the reservation manually in the opened browser window.",
            })

    except Exception as exc:
        logger.warning("Google Maps fallback failed: %s", exc)
        _emit(event_queue, "result", {
            "success": False,
            "error": f"Could not complete booking automatically: {exc}",
        })


def _run_stub_booking(
    restaurant_name: str,
    city: str,
    date: str,
    time_str: str,
    party_size: int,
    event_queue: queue.Queue,
    resume_event: threading.Event,
    resume_data_slot: list,
    cancel_event: threading.Event,
) -> None:
    """
    Stub mode when nova_act is not installed.
    Simulates the booking flow for development/testing.
    """
    import time as _time

    steps = [
        ("Opened Tabelog (stub mode)", "info"),
        (f"Searched for {restaurant_name} in {city}", "action"),
        ("Clicked most relevant result", "action"),
        ("Found reservation button (予約する)", "action"),
        (f"Set date={date}, time={time_str}, party={party_size}", "action"),
    ]

    for message, log_type in steps:
        if _check_cancelled(cancel_event):
            return
        _emit_log(event_queue, message, log_type)
        _time.sleep(0.5)

    _emit(event_queue, "needs_user_input", {"fields": ["phone"]})
    _emit_log(event_queue, "Waiting for phone number (stub)...", "info")

    resume_event.wait(timeout=BOOKING_TIMEOUT_SECONDS)
    if _check_cancelled(cancel_event):
        return
    if not resume_event.is_set():
        _emit(event_queue, "error", {"message": "Timed out waiting for phone (stub)"})
        return

    _emit_log(event_queue, "Phone received, submitting form (stub)...", "action")
    _time.sleep(1.0)

    _emit_log(event_queue, "Form submitted successfully (stub)", "success")
    _emit(event_queue, "result", {
        "success": True,
        "details": {
            "confirmation_number": "STUB-12345",
            "booking_time": f"{date} {time_str}",
            "notes": f"STUB booking for {party_size} people at {restaurant_name}",
        },
    })
