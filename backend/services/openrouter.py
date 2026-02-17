"""
NovaSync — OpenRouter service (temporary).
Drop-in replacement for bedrock.py that uses OpenRouter's chat completion
API with tool-calling to extract structured itinerary nodes.
Swap back to bedrock.py once Nova credits are available.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import httpx

from models import ItineraryNode

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Tool specification (OpenAI-compatible format) ───────────────────────────
EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_itinerary",
        "description": (
            "Extract structured itinerary nodes from a raw travel idea. "
            "Each node represents one activity or destination."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "description": "List of extracted itinerary activities",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Short, descriptive name of the activity or destination",
                            },
                            "activity_type": {
                                "type": "string",
                                "description": (
                                    "Category of the activity. One of: "
                                    "sightseeing, hiking, food, transport, "
                                    "accommodation, adventure, culture, shopping, relaxation"
                                ),
                            },
                            "duration_mins": {
                                "type": "integer",
                                "description": "Estimated duration in minutes",
                            },
                            "date_local": {
                                "type": "string",
                                "description": "Planned local date in YYYY-MM-DD format",
                            },
                            "start_time_local": {
                                "type": "string",
                                "description": "Planned local start time in 24h HH:MM format",
                            },
                            "end_time_local": {
                                "type": "string",
                                "description": "Planned local end time in 24h HH:MM format",
                            },
                            "lat": {
                                "type": "number",
                                "description": "Latitude of the location (if known)",
                            },
                            "long": {
                                "type": "number",
                                "description": "Longitude of the location (if known)",
                            },
                            "description": {
                                "type": "string",
                                "description": "A brief, helpful description of the activity",
                            },
                        },
                        "required": ["title", "activity_type"],
                    },
                },
            },
            "required": ["nodes"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are NovaSync, a travel planning assistant. "
    "The user will give you a raw, unstructured travel idea dump and may include images/videos. "
    "Use the text and any uploaded media together while planning. "
    "Your job is to extract every distinct activity or destination and "
    "return them as structured itinerary nodes using the extract_itinerary tool. "
    "Return nodes in a practical chronological order for a trip itinerary. "
    "If trip context includes dates/timezone, assign date_local and local start/end times for each node. "
    "Estimate durations, supply coordinates if you know them, and write concise descriptions. "
    "Do NOT make up activities that are not in the text/media input."
)


@dataclass(frozen=True)
class MediaInput:
    filename: str
    mime_type: str
    data_url: str


# ── Public API ──────────────────────────────────────────────────────────────
def extract_itinerary(
    raw_idea: str,
    media_inputs: list[MediaInput] | None = None,
) -> list[ItineraryNode]:
    """Call OpenRouter with forced tool use and return parsed ItineraryNode list."""

    # Fetch env vars inside function to ensure load_dotenv() has run
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    model = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set in environment or .env file")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    user_content: list[dict[str, object]] = [{"type": "text", "text": raw_idea}]
    for media in media_inputs or []:
        if media.mime_type.startswith("image/"):
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": media.data_url},
                }
            )
        elif media.mime_type.startswith("video/"):
            user_content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": media.data_url},
                }
            )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [EXTRACT_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "extract_itinerary"}},
    }

    with httpx.Client(timeout=60.0) as client:
        try:
            resp = client.post(OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise ValueError(
                "OpenRouter request failed "
                f"({exc.response.status_code}): {detail}"
            ) from exc
        data = resp.json()

    logger.info("OpenRouter response model: %s", data.get("model"))

    # Parse the tool call from the response
    message = data["choices"][0]["message"]
    tool_calls = message.get("tool_calls", [])

    if not tool_calls:
        raise ValueError(
            "OpenRouter did not return a tool call. "
            f"Full response: {json.dumps(data, default=str)}"
        )

    arguments_str = tool_calls[0]["function"]["arguments"]
    arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
    nodes_raw = arguments.get("nodes", [])

    return [ItineraryNode(**node) for node in nodes_raw]
