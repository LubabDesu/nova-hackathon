"""
NovaSync — per-traveler preference extraction worker.

Calls the LLM to extract/consolidate preferences from free-text notes
into structured InputDirectives. Fail-open: on any error, returns the
raw submitted directives unchanged.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

from models import InputDirectives, TravelerProfile

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def extract_traveler_profile(
    *,
    nickname: str,
    input_directives: InputDirectives,
    free_text: str,
    trip_location: str | None,
) -> TravelerProfile:
    """
    Extract consolidated InputDirectives for one traveler via LLM.
    Always returns a TravelerProfile — never raises.
    """
    try:
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            logger.warning("OPENROUTER_API_KEY not set; returning raw directives for %s", nickname)
            return TravelerProfile(
                nickname=nickname,
                extracted_directives=input_directives,
                raw_free_text=free_text,
            )

        model = os.environ.get("EXTRACTION_MODEL") or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        location_str = trip_location or "not specified"

        schema = {
            "type": "object",
            "properties": {
                "hard_constraints": {"type": "array", "items": {"type": "string"}},
                "soft_preferences": {"type": "array", "items": {"type": "string"}},
                "must_include": {"type": "array", "items": {"type": "string"}},
                "avoid": {"type": "array", "items": {"type": "string"}},
                "mobility_mode": {"type": ["string", "null"]},
                "budget_level": {"type": ["string", "null"]},
                "pace": {"type": ["string", "null"]},
                "travel_party": {"type": "array", "items": {"type": "string"}},
                "dietary": {"type": "array", "items": {"type": "string"}},
                "wake_time_pref": {"type": ["string", "null"]},
                "fitness_level": {"type": ["string", "null"]},
                "accommodation_style": {"type": ["string", "null"]},
            },
        }

        prompt = (
            f"You are a travel preference extractor.\n"
            f"Traveler: {nickname}\n"
            f"Destination: {location_str}\n\n"
            f"Submitted preferences (JSON):\n{input_directives.model_dump_json(indent=2)}\n\n"
            f"Free-text notes:\n{free_text or '(none)'}\n\n"
            "Return ONLY a valid JSON object matching this schema (no markdown, no explanation):\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            "Merge any preferences from the free text into the JSON. Keep all existing constraints. "
            "Only add or extend fields — never remove. Return the merged result."
        )

        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()

        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown fences if present
        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        parsed = json.loads(content)
        extracted = InputDirectives.model_validate(parsed)
        logger.info("Extracted directives for traveler %s", nickname)
        return TravelerProfile(
            nickname=nickname,
            extracted_directives=extracted,
            raw_free_text=free_text,
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "preference_extraction_worker failed for %s (fail-open): %s",
            nickname,
            exc,
        )
        return TravelerProfile(
            nickname=nickname,
            extracted_directives=input_directives,
            raw_free_text=free_text,
        )
