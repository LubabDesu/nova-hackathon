"""
Weather tool for Nova planning agent.
Uses Open-Meteo free API with Nominatim geocoding.
No API key required.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes
WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def get_weather(city: str, start_date: str, end_date: str) -> str:
    """
    Get weather forecast for a city between start_date and end_date.

    Args:
        city: City name (e.g. 'Tokyo')
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD

    Returns:
        Formatted weather forecast per day, or error message.
    """
    try:
        lat, lon = _geocode_city(city)
    except Exception as exc:  # noqa: BLE001
        return f"Could not geocode {city}: {exc}"

    try:
        return _fetch_weather(lat, lon, city, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        return f"Weather fetch failed for {city}: {exc}"


def _geocode_city(city: str) -> tuple[float, float]:
    """Use Nominatim to get lat/lon for a city. Returns (lat, lon)."""
    headers = {"User-Agent": "NovaSync/1.0 travel-planner"}
    params = {
        "q": city,
        "format": "json",
        "limit": "1",
    }
    with httpx.Client(timeout=6.0) as client:
        resp = client.get(NOMINATIM_URL, params=params, headers=headers)
        resp.raise_for_status()
        results = resp.json()

    if not results:
        raise ValueError(f"City not found: {city}")

    return float(results[0]["lat"]), float(results[0]["lon"])


def _fetch_weather(
    lat: float,
    lon: float,
    city: str,
    start_date: str,
    end_date: str,
) -> str:
    """Fetch daily weather from Open-Meteo."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
        "timezone": "auto",
        "start_date": start_date,
        "end_date": end_date,
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    codes = daily.get("weathercode", [])

    if not dates:
        return f"No weather data available for {city} from {start_date} to {end_date}."

    lines: list[str] = [f"Weather forecast for {city}:"]
    for i, date_str in enumerate(dates):
        temp_max = max_temps[i] if i < len(max_temps) else None
        temp_min = min_temps[i] if i < len(min_temps) else None
        rain = precip[i] if i < len(precip) else None
        code = codes[i] if i < len(codes) else None

        condition = WMO_CODES.get(int(code), "Unknown") if code is not None else "Unknown"
        temp_str = ""
        if temp_max is not None and temp_min is not None:
            temp_str = f"{int(temp_min)}\u2013{int(temp_max)}\u00b0C"
        elif temp_max is not None:
            temp_str = f"{int(temp_max)}\u00b0C"

        rain_str = ""
        if rain is not None and rain > 0:
            rain_str = f", {rain}mm rain"

        # Outdoor suitability hint
        is_bad = code is not None and int(code) in {45, 48, 51, 53, 55, 61, 63, 65, 71, 73, 75, 80, 81, 82, 95, 96, 99}
        hint = " -- Plan indoors" if is_bad else ""

        lines.append(f"  {date_str}: {temp_str}, {condition}{rain_str}{hint}")

    return "\n".join(lines)
