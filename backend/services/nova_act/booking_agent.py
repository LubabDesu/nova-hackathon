"""
Nova Act Japanese restaurant booking agent.
Wraps the run_booking_agent function for use as a class-based interface.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from services.nova_booking_agent import run_booking_agent


@dataclass
class BookingResult:
    success: bool
    confirmation_number: str | None = None
    booking_time: str | None = None
    error: str | None = None
    notes: str | None = None


class JapaneseRestaurantBookingAgent:
    """Class interface for the Nova Act restaurant booking agent."""

    def __init__(
        self,
        restaurant_name: str,
        city: str,
        date: str,
        time: str,
        party_size: int,
    ) -> None:
        self.restaurant_name = restaurant_name
        self.city = city
        self.date = date
        self.time = time
        self.party_size = party_size
        self.event_queue: queue.Queue = queue.Queue()
        self.resume_event = threading.Event()
        self.resume_data_slot: list[dict | None] = [None]
        self.cancel_event = threading.Event()

    def start(self) -> threading.Thread:
        """Spawn the booking agent thread and return it."""
        thread = threading.Thread(
            target=run_booking_agent,
            args=(
                self.restaurant_name,
                self.city,
                self.date,
                self.time,
                self.party_size,
                self.event_queue,
                self.resume_event,
                self.resume_data_slot,
                self.cancel_event,
            ),
            daemon=True,
        )
        thread.start()
        return thread
