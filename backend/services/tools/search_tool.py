"""
Search tool for Nova planning agent.
Wraps Tavily/Brave search with graceful fallback.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def search_activities(location: str, query: str) -> str:
    """
    Search for activities, restaurants, and attractions at a destination.
    Returns formatted results as a string.
    """
    from services.workers.web_research_worker import _search_tavily, _search_brave, _search_duckduckgo

    # Only append location if not already present (avoids "Kyoto 2026 Kyoto, Japan")
    if not location or not location.strip():
        logger.warning("search_activities called with empty location for query: %s", query)
        full_query = query.strip()
    elif location.lower() in query.lower():
        full_query = query.strip()
    else:
        full_query = f"{query} {location}".strip()
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()

    results: list[dict[str, str]] = []
    try:
        if tavily_key:
            results = _search_tavily(full_query, tavily_key)
        elif brave_key:
            results = _search_brave(full_query, brave_key)
        else:
            results = _search_duckduckgo(full_query)
    except Exception as exc:  # noqa: BLE001
        logger.warning("search_activities failed: %s", exc)
        return f"Search failed: {exc}"

    if not results:
        return f"No results found for '{full_query}'"

    lines: list[str] = []
    for item in results[:5]:
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        url = (item.get("url") or "").strip()
        if title and snippet:
            lines.append(f"{title}: {snippet[:200]}")
        elif title:
            lines.append(title)
        if url:
            lines.append(f"  Source: {url}")

    return "\n".join(lines) if lines else f"Found {len(results)} results but no text content."
