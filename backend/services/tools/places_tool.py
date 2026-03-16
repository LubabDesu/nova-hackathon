"""
Place validation tool for Nova planning agent.
Uses Nominatim OpenStreetMap (free, no API key required).
Rate limit: 1 request/second -- enforced via time.sleep(1).
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"

# Track last request time for rate limiting
_last_request_time: float = 0.0


def validate_place(place_name: str, location: str) -> str:
    """
    Validate that a specific place exists and get its coordinates.

    Args:
        place_name: Name of the place (e.g. 'teamLab Planets Tokyo')
        location: City/region context (e.g. 'Tokyo, Japan')

    Returns:
        Confirmation string with lat/lon, or 'Not found' message.
    """
    global _last_request_time  # noqa: PLW0603

    # Enforce 1 req/sec rate limit for Nominatim
    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    query = f"{place_name}, {location}"
    headers = {"User-Agent": "NovaSync/1.0 travel-planner"}
    params = {
        "q": query,
        "format": "json",
        "limit": "1",
        "addressdetails": "1",
    }

    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(NOMINATIM_SEARCH_URL, params=params, headers=headers)
            resp.raise_for_status()
            results = resp.json()
        _last_request_time = time.time()

        if not results:
            return f"Not found: '{place_name}' could not be verified in {location}. Verify the name before including it in the plan."

        result = results[0]
        display_name = result.get("display_name", place_name)
        lat = float(result.get("lat", 0))
        lon = float(result.get("lon", 0))

        return f"Found: {display_name} | lat={lat:.4f}, lon={lon:.4f}"

    except Exception as exc:  # noqa: BLE001
        logger.warning("validate_place failed for '%s': %s", place_name, exc)
        return f"Place validation error for '{place_name}': {exc}"
