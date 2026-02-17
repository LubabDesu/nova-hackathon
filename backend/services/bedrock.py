"""
NovaSync — Amazon Bedrock service.
Uses the Converse API with forced tool-calling to extract structured
itinerary nodes from unstructured travel ideas via Amazon Nova Pro.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import bedrock_runtime
from models import ItineraryNode

logger = logging.getLogger(__name__)

MODEL_ID = "amazon.nova-lite-v1:0"

# ── Tool specification ──────────────────────────────────────────────────────
# This schema is what Nova Pro is *forced* to fill in via toolChoice: {"any": {}}.
EXTRACT_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "extract_itinerary",
        "description": (
            "Extract structured itinerary nodes from a raw travel idea. "
            "Each node represents one activity or destination."
        ),
        "inputSchema": {
            "json": {
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
    },
}

SYSTEM_PROMPT = (
    "You are NovaSync, a travel planning assistant. "
    "The user will give you a raw, unstructured travel idea dump. "
    "Your job is to extract every distinct activity or destination and "
    "return them as structured itinerary nodes using the extract_itinerary tool. "
    "Estimate durations, supply coordinates if you know them, and write concise descriptions. "
    "Do NOT make up activities that are not in the input."
)


# ── Public API ──────────────────────────────────────────────────────────────
def extract_itinerary(raw_idea: str) -> list[ItineraryNode]:
    """Call Nova Pro with forced tool use and return parsed ItineraryNode list."""

    response = bedrock_runtime.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [{"text": raw_idea}],
            },
        ],
        toolConfig={
            "tools": [EXTRACT_TOOL],
            "toolChoice": {"any": {}},
        },
    )

    # The response contains a tool-use block because we forced it.
    stop_reason = response.get("stopReason")
    logger.info("Bedrock stop reason: %s", stop_reason)

    tool_use_block = _find_tool_use_block(response)
    nodes_raw: list[dict[str, Any]] = tool_use_block.get("input", {}).get("nodes", [])

    return [ItineraryNode(**node) for node in nodes_raw]


# ── Helpers ─────────────────────────────────────────────────────────────────
def _find_tool_use_block(response: dict) -> dict[str, Any]:
    """Walk the Converse response and locate the first toolUse content block."""
    for block in response.get("output", {}).get("message", {}).get("content", []):
        if "toolUse" in block:
            return block["toolUse"]
    raise ValueError(
        "Nova Pro did not return a toolUse block. "
        f"Full response: {json.dumps(response, default=str)}"
    )
