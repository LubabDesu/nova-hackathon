"""
Local events tool for Nova planning agent.
Uses Ticketmaster Discovery API with fallback to search.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


def get_local_events(
    location: str,
    start_date: str,
    end_date: str,
    category: str | None = None,
) -> str:
    """
    Find real events at a location between start_date and end_date.

    Args:
        location: City name (e.g. 'Tokyo')
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        category: Optional category filter (e.g. 'music', 'sports')

    Returns:
        Formatted string listing events, or informative message if unavailable.
    """
    api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()

    if not api_key:
        # Graceful degradation: fall back to search
        return _events_via_search(location, start_date, end_date, category)

    try:
        return _ticketmaster_events(api_key, location, start_date, end_date, category)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ticketmaster failed, falling back to search: %s", exc)
        return _events_via_search(location, start_date, end_date, category)


def _ticketmaster_events(
    api_key: str,
    location: str,
    start_date: str,
    end_date: str,
    category: str | None,
) -> str:
    params: dict[str, str] = {
        "apikey": api_key,
        "city": location,
        "startDateTime": f"{start_date}T00:00:00Z",
        "endDateTime": f"{end_date}T23:59:59Z",
        "size": "10",
        "sort": "date,asc",
        "locale": "*",
    }
    if category:
        params["classificationName"] = category

    with httpx.Client(timeout=8.0) as client:
        resp = client.get(TICKETMASTER_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    events = (
        data.get("_embedded", {}).get("events", [])
        if "_embedded" in data
        else []
    )

    if not events:
        return f"No events found in {location} from {start_date} to {end_date}."

    lines: list[str] = [f"Events in {location} ({start_date} to {end_date}):"]
    for event in events[:8]:
        name = event.get("name", "Unknown Event")
        # Date
        dates = event.get("dates", {}).get("start", {})
        event_date = dates.get("localDate", "Date TBD")
        # Venue
        venues = event.get("_embedded", {}).get("venues", [])
        venue = venues[0].get("name", "Venue TBD") if venues else "Venue TBD"
        # Classification
        classifications = event.get("classifications", [])
        genre = ""
        if classifications:
            segment = classifications[0].get("segment", {}).get("name", "")
            genre_name = classifications[0].get("genre", {}).get("name", "")
            if genre_name and genre_name != "Undefined":
                genre = f" [{genre_name}]"
            elif segment and segment != "Undefined":
                genre = f" [{segment}]"

        lines.append(f"  {event_date}: {name}{genre} @ {venue}")

    return "\n".join(lines)


def _events_via_search(
    location: str,
    start_date: str,
    end_date: str,
    category: str | None,
) -> str:
    """Fallback: search for events when Ticketmaster is unavailable."""
    try:
        from services.tools.search_tool import search_activities
        cat_str = category or "events festivals concerts"
        result = search_activities(location, f"{cat_str} {start_date}")
        if result and not result.startswith("No results") and not result.startswith("Search failed"):
            return f"Events search for {location} ({start_date}\u2013{end_date}):\n{result}"
        return f"No events information available for {location} during {start_date}\u2013{end_date}."
    except Exception as exc:  # noqa: BLE001
        return f"Events lookup unavailable: {exc}"
