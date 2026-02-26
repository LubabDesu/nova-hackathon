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
from typing import Any

import httpx

from models import ItineraryNode

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_IMAGE_MODEL = "qwen/qwen3-vl-30b-a3b-thinking"
DEFAULT_OPENROUTER_FALLBACK_MODEL = "nvidia/nemotron-3-nano-30b-a3b:free"
DEFAULT_OPENROUTER_QUERY_BUILDER_MODEL = "google/gemma-3n-e2b-it:free"
DEFAULT_OPENROUTER_SCAFFOLD_TIMEOUT_SECONDS = 25.0

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

MEDIA_CONTEXT_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_media_context",
        "description": (
            "Extract concise, planning-relevant visual context from uploaded travel media."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "visual_facts": {
                    "type": "array",
                    "description": "Concise visual findings grounded in uploaded media.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_filename": {
                                "type": "string",
                                "description": "Filename from the uploaded media when identifiable.",
                            },
                            "summary": {
                                "type": "string",
                                "description": "One concise sentence describing the visual finding.",
                            },
                            "location_cues": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Location or venue cues inferred from the media.",
                            },
                            "activity_hints": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Activity hints visible in the media.",
                            },
                            "vibe_tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Mood/style tags inferred from visuals.",
                            },
                            "constraints": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Planning constraints suggested by visuals.",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence score from 0 to 1.",
                            },
                        },
                        "required": ["summary"],
                    },
                }
            },
            "required": ["visual_facts"],
        },
    },
}

SYSTEM_PROMPT = (
    "You are NovaSync, a travel planning assistant. "
    "The user will give you a raw, unstructured travel idea dump plus normalized context evidence. "
    "Use only the provided text/context evidence while planning. "
    "Your job is to extract every distinct activity or destination and "
    "return them as structured itinerary nodes using the extract_itinerary tool. "
    "Return nodes in a practical chronological order for a trip itinerary. "
    "If trip context includes dates/timezone, assign date_local and local start/end times for each node. "
    "Estimate durations, supply coordinates if you know them, and write concise descriptions. "
    "Do NOT make up activities that are not in the text/media input."
)

MEDIA_CONTEXT_SYSTEM_PROMPT = (
    "You are NovaSync vision context extractor. "
    "Read uploaded travel images/videos and return concise planning signals only. "
    "Do not generate itineraries or schedules. "
    "Return only grounded visual facts in extract_media_context. "
    "If uncertain, return fewer items and lower confidence."
)

QUERY_BUILDER_SYSTEM_PROMPT = (
    "You are NovaSync's senior travel concierge researcher, an expert at converting messy trip ideas "
    "into high-signal web search strings for itinerary planning. "
    "Think silently using this checklist before writing queries: "
    "(1) identify concrete entities and classify each as place/activity/route, "
    "(2) infer user-POV needs (booking, timing, accessibility, transport, safety constraints), "
    "(3) generate compact search strings that can directly retrieve operational facts. "
    "Do not output your reasoning or checklist. "
    "Prefer specific, entity-anchored, operational queries over broad trip phrasing. "
    "Use search-string style (keyword phrases), not full-sentence questions. "
    "Avoid generic wording like 'romantic trip', 'how to', 'find', or 'check' unless no concrete entity exists. "
    "If named places or activities are present, anchor queries to them. "
    "Return concise high-value search queries for itinerary grounding. "
    "Output strict JSON only, no markdown, no prose. "
    'Schema: {"queries":[{"q":"string","intent":"official_site|hours|booking|best_time|safety|transport|food|activities"}]}. '
    "Few-shot examples (style only): "
    "Bad: 'What are the best times to visit MONA and Wombat Patting in Tasmania?'. "
    "Good: 'Tokyo Tower best time to visit, weekday crowds', intent=best_time. "
    "Good: 'Marina Bay Sands infinity pool official ticket booking', intent=booking. "
    "Good: 'panda visiting China where and when', intent=activities. "
    "Good: 'transport options around hobart tasmania', intent=transport."
)

PLANNING_SCAFFOLD_SYSTEM_PROMPT = (
    "You are NovaSync itinerary strategist. "
    "Produce a concise natural-language draft itinerary scaffold before JSON extraction. "
    "Do not reveal hidden reasoning and do not output chain-of-thought. "
    "Output only an actionable plan outline with these sections:\n"
    "1) Trip frame (1-2 lines)\n"
    "2) Day-by-day bullets with time windows and activity flow\n"
    "3) Constraint checks (hard constraints, avoid, safety)\n"
    "4) Open questions/assumptions (if any)\n"
    "Keep it compact, specific, and evidence-grounded."
    "Verbalise your reasoning process before outputting the JSON, first start off by considering must-do activites in the location of travel"
    "ensure the plan is contextually relevant to the user's input."
)


@dataclass(frozen=True)
class MediaInput:
    filename: str
    mime_type: str
    data_url: str


@dataclass(frozen=True)
class MediaSignal:
    source_filename: str | None
    summary: str
    location_cues: list[str]
    activity_hints: list[str]
    vibe_tags: list[str]
    constraints: list[str]
    confidence: float


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _resolve_model_sequence(primary_model: str, *, has_image_input: bool) -> list[str]:
    """Resolve ordered model list from env, with image-aware routing and fallbacks."""
    models: list[str] = []

    if has_image_input:
        image_model = os.environ.get("OPENROUTER_IMAGE_MODEL", "").strip()
        if not image_model:
            image_model = DEFAULT_OPENROUTER_IMAGE_MODEL
        models.append(image_model)

    models.append(primary_model)

    single_fallback = os.environ.get("OPENROUTER_FALLBACK_MODEL", "").strip()
    fallback_models_raw = os.environ.get("OPENROUTER_FALLBACK_MODELS", "")
    fallback_models = [item.strip() for item in fallback_models_raw.split(",") if item.strip()]
    if single_fallback:
        fallback_models.insert(0, single_fallback)
    if not fallback_models:
        fallback_models = [DEFAULT_OPENROUTER_FALLBACK_MODEL]
    models.extend(fallback_models)
    return _dedupe_keep_order(models)


def _build_payload(
    *,
    model: str,
    user_content: list[dict[str, object]],
    force_tool_choice: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "tools": [EXTRACT_TOOL],
    }
    if force_tool_choice:
        payload["tool_choice"] = {
            "type": "function",
            "function": {"name": "extract_itinerary"},
        }
    return payload


def _post_openrouter(
    *,
    client: httpx.Client,
    payload: dict[str, Any],
    headers: dict[str, str],
    purpose: str = "unknown",
) -> dict[str, Any]:
    model = payload.get("model", "unknown")
    logger.info("→ OpenRouter POST | purpose=%-22s | model=%s", purpose, model)
    resp = client.post(OPENROUTER_URL, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    response_model = data.get("model") or model
    logger.info("← OpenRouter OK  | purpose=%-22s | model=%s", purpose, response_model)
    return data


def _extract_nodes_from_response(data: dict[str, Any]) -> list[ItineraryNode]:
    arguments = _extract_tool_arguments(data, tool_name="extract_itinerary")
    nodes_raw = arguments.get("nodes", [])
    normalized_nodes_raw = _normalize_nodes_payload(nodes_raw)

    parsed_nodes: list[ItineraryNode] = []
    skipped_items: list[str] = []
    for index, raw_node in enumerate(normalized_nodes_raw):
        node_mapping = _coerce_node_mapping(raw_node)
        if node_mapping is None:
            skipped_items.append(
                f"index={index} type={type(raw_node).__name__} value={str(raw_node)[:120]}"
            )
            continue

        try:
            parsed_nodes.append(ItineraryNode(**node_mapping))
        except Exception as exc:  # noqa: BLE001
            skipped_items.append(
                f"index={index} validation_error={exc} value={str(node_mapping)[:180]}"
            )

    if skipped_items:
        logger.warning(
            "OpenRouter returned partially invalid nodes payload; skipped %d item(s). Samples: %s",
            len(skipped_items),
            " | ".join(skipped_items[:3]),
        )
    if not parsed_nodes:
        raise ValueError(
            "OpenRouter returned no valid itinerary nodes after normalization. "
            f"Raw arguments excerpt: {str(arguments)[:800]}"
        )
    return parsed_nodes


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if not lines:
        return text
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    joined = "\n".join(lines).strip()
    if joined.lower().startswith("json"):
        joined = joined[4:].strip()
    return joined


def _maybe_parse_json(value: str) -> Any:
    text = _strip_code_fence(value)
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _normalize_nodes_payload(nodes_raw: Any) -> list[Any]:
    if isinstance(nodes_raw, str):
        parsed = _maybe_parse_json(nodes_raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            nested_nodes = parsed.get("nodes")
            if isinstance(nested_nodes, list):
                return nested_nodes
            return [parsed]
        return [nodes_raw]

    if isinstance(nodes_raw, dict):
        nested_nodes = nodes_raw.get("nodes")
        if isinstance(nested_nodes, list):
            return nested_nodes
        return [nodes_raw]

    if isinstance(nodes_raw, list):
        return nodes_raw

    return []


def _coerce_node_mapping(raw_node: Any) -> dict[str, Any] | None:
    if isinstance(raw_node, dict):
        return raw_node

    if isinstance(raw_node, str):
        parsed = _maybe_parse_json(raw_node)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
        return None

    return None


def _extract_tool_arguments(data: dict[str, Any], *, tool_name: str) -> dict[str, Any]:
    if "choices" not in data or not data["choices"]:
        error_body = data.get("error") or data
        raise ValueError(
            f"OpenRouter response missing 'choices'. Possible quota, model, or API error: "
            f"{json.dumps(error_body, default=str)[:600]}"
        )
    message = data["choices"][0]["message"]
    tool_calls = message.get("tool_calls", [])

    if not tool_calls:
        raise ValueError(
            "OpenRouter did not return a tool call. "
            f"Full response: {json.dumps(data, default=str)}"
        )

    for tool_call in tool_calls:
        fn = tool_call.get("function", {})
        if fn.get("name") != tool_name:
            continue
        arguments_str = fn.get("arguments", {})
        return (
            json.loads(arguments_str)
            if isinstance(arguments_str, str)
            else arguments_str
        )

    raise ValueError(
        f"OpenRouter did not return expected tool '{tool_name}'. "
        f"Full response: {json.dumps(data, default=str)}"
    )


def _should_retry_without_forced_tool_choice(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code != 404:
        return False
    detail = exc.response.text.lower()
    return "tool_choice" in detail and "no endpoints found" in detail


def _message_content_to_text(data: dict[str, Any]) -> str:
    message = data.get("choices", [{}])[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, dict):
                text = chunk.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(chunk, str):
                parts.append(chunk)
        return "\n".join(parts)
    return str(content)


def _extract_first_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if not text:
        raise ValueError("Query builder returned empty response text.")

    fence_start = text.find("```")
    if fence_start >= 0:
        fence_end = text.find("```", fence_start + 3)
        if fence_end > fence_start:
            fenced = text[fence_start + 3 : fence_end].strip()
            if fenced.lower().startswith("json"):
                fenced = fenced[4:].strip()
            text = fenced or text

    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        text = text[first : last + 1]

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Query builder JSON root is not an object.")
    return parsed


def _sanitize_query(value: str) -> str:
    query = " ".join(value.split()).strip()
    if not query:
        return ""
    if len(query) > 120:
        query = query[:120].rstrip()
    if len(query) < 6:
        return ""
    return query


def _resolve_query_builder_models(primary_model: str) -> list[str]:
    models: list[str] = [primary_model]

    single_fallback = os.environ.get(
        "OPENROUTER_QUERY_BUILDER_FALLBACK_MODEL",
        "",
    ).strip()
    fallback_models_raw = os.environ.get("OPENROUTER_QUERY_BUILDER_FALLBACK_MODELS", "")
    fallback_models = [item.strip() for item in fallback_models_raw.split(",") if item.strip()]
    if single_fallback:
        fallback_models.insert(0, single_fallback)

    models.extend(fallback_models)
    return _dedupe_keep_order(models)


def _resolve_scaffold_models(primary_model: str) -> list[str]:
    models: list[str] = [primary_model]

    single_fallback = os.environ.get(
        "OPENROUTER_COT_FALLBACK_MODEL",
        "",
    ).strip()
    fallback_models_raw = os.environ.get("OPENROUTER_COT_FALLBACK_MODELS", "")
    fallback_models = [item.strip() for item in fallback_models_raw.split(",") if item.strip()]
    if single_fallback:
        fallback_models.insert(0, single_fallback)
    if not fallback_models:
        global_fallbacks = os.environ.get("OPENROUTER_FALLBACK_MODELS", "")
        fallback_models = [
            item.strip()
            for item in global_fallbacks.split(",")
            if item.strip()
        ]

    models.extend(fallback_models)
    return _dedupe_keep_order(models)


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        parsed = default
    return max(min_value, min(parsed, max_value))


def _sanitize_planning_scaffold(raw_text: str) -> str:
    text = _strip_code_fence(raw_text)
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    cleaned = "\n".join(lines).strip()
    return cleaned


# ── Public API ──────────────────────────────────────────────────────────────
def extract_itinerary(
    raw_idea: str,
    media_inputs: list[MediaInput] | None = None,
) -> list[ItineraryNode]:
    """Call OpenRouter with tool use and retry across fallback models when needed."""

    # Fetch env vars inside function to ensure load_dotenv() has run
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    primary_model = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()
    has_image_input = any(
        media.mime_type.startswith("image/")
        for media in (media_inputs or [])
    )
    models = _resolve_model_sequence(primary_model, has_image_input=has_image_input)

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

    errors: list[str] = []
    with httpx.Client(timeout=60.0) as client:
        if has_image_input:
            logger.info(
                "Image input detected; trying image-capable model first: %s",
                models[0],
            )
        for model in models:
            allow_unforced_retry = model == models[0]
            # First attempt: force tool call (best for structured extraction).
            for force_tool_choice in (True, False):
                if not force_tool_choice and allow_unforced_retry:
                    # Compatibility retry when routed endpoint rejects forced tool_choice.
                    logger.info(
                        "Retrying model without forced tool_choice for compatibility: %s",
                        model,
                    )
                elif not force_tool_choice:
                    # By default, models use forced tool calls; skip unforced retries unless needed.
                    continue

                payload = _build_payload(
                    model=model,
                    user_content=user_content,
                    force_tool_choice=force_tool_choice,
                )
                try:
                    data = _post_openrouter(client=client, payload=payload, headers=headers, purpose="itinerary_extraction")
                    return _extract_nodes_from_response(data)
                except httpx.HTTPStatusError as exc:
                    detail = exc.response.text[:500]
                    errors.append(
                        "request failed "
                        f"(model={model}, force_tool_choice={force_tool_choice}, "
                        f"status={exc.response.status_code}): {detail}"
                    )

                    can_retry_compat = (
                        force_tool_choice
                        and _should_retry_without_forced_tool_choice(exc)
                    )
                    if can_retry_compat:
                        allow_unforced_retry = True
                        continue
                    break
                except ValueError as exc:
                    errors.append(
                        "response parse failed "
                        f"(model={model}, force_tool_choice={force_tool_choice}): {exc}"
                    )
                    break

    joined_errors = " | ".join(errors[-4:]) if errors else "No attempt details available."
    raise ValueError(
        "OpenRouter extraction failed across all configured model attempts. "
        f"Set OPENROUTER_MODEL and optional OPENROUTER_FALLBACK_MODEL(S). Details: {joined_errors}"
    )


def build_planning_scaffold(
    *,
    scaffold_prompt: str,
) -> tuple[str | None, dict[str, Any]]:
    """
    Generate a concise natural-language itinerary scaffold before JSON extraction.

    Returns scaffold text (or None) plus debug metadata. This stage should fail-open.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None, {"error": "OPENROUTER_API_KEY is not set."}

    primary_model = os.environ.get("OPENROUTER_COT_MODEL", "").strip()
    if not primary_model:
        primary_model = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()
    models = _resolve_scaffold_models(primary_model)

    timeout_seconds_raw = os.environ.get("OPENROUTER_COT_TIMEOUT_SECONDS", "")
    try:
        timeout_seconds = (
            float(timeout_seconds_raw)
            if timeout_seconds_raw
            else DEFAULT_OPENROUTER_SCAFFOLD_TIMEOUT_SECONDS
        )
    except ValueError:
        timeout_seconds = DEFAULT_OPENROUTER_SCAFFOLD_TIMEOUT_SECONDS
    timeout_seconds = max(5.0, min(timeout_seconds, 90.0))

    prompt_max_chars = _env_int(
        "OPENROUTER_COT_PROMPT_MAX_CHARS",
        6500,
        min_value=600,
        max_value=18000,
    )
    output_max_chars = _env_int(
        "OPENROUTER_COT_OUTPUT_MAX_CHARS",
        2400,
        min_value=300,
        max_value=8000,
    )

    prompt_text = scaffold_prompt.strip()
    prompt_text_for_model = prompt_text[:prompt_max_chars]
    prompt_was_truncated = len(prompt_text_for_model) < len(prompt_text)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    scaffold_text: str | None = None
    error: str | None = None
    error_body: str | None = None
    selected_model: str | None = None
    selected_response_model: str | None = None
    temperature_used: float | None = None
    raw_response_text: str = ""
    attempts: list[dict[str, Any]] = []

    with httpx.Client(timeout=timeout_seconds) as client:
        for model in models:
            for include_temperature in (True, False):
                attempt: dict[str, Any] = {
                    "model": model,
                    "include_temperature": include_temperature,
                }
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": PLANNING_SCAFFOLD_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt_text_for_model},
                    ],
                }
                if include_temperature:
                    payload["temperature"] = 0.2

                try:
                    data = _post_openrouter(client=client, payload=payload, headers=headers, purpose="scaffold")
                    raw_response_text = _message_content_to_text(data)
                    candidate = _sanitize_planning_scaffold(raw_response_text)
                    attempt["status"] = "ok"
                    attempt["response_model"] = data.get("model")
                    attempt["scaffold_chars"] = len(candidate)
                    attempts.append(attempt)
                    if candidate:
                        scaffold_text = candidate[:output_max_chars]
                        selected_model = model
                        selected_response_model = str(data.get("model", ""))
                        temperature_used = 0.2 if include_temperature else None
                        break
                    error = "Scaffold model returned empty text."
                except httpx.HTTPStatusError as exc:
                    detail = exc.response.text[:1500]
                    error = (
                        f"HTTP {exc.response.status_code} from OpenRouter "
                        f"(model={model}, include_temperature={include_temperature})"
                    )
                    error_body = detail
                    attempt["status"] = "http_error"
                    attempt["http_status"] = exc.response.status_code
                    attempt["error_body_excerpt"] = detail
                    attempts.append(attempt)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                    attempt["status"] = "exception"
                    attempt["error"] = str(exc)
                    attempts.append(attempt)

            if scaffold_text:
                break

    debug = {
        "scaffold_enabled": True,
        "scaffold_model_requested": primary_model,
        "scaffold_models_attempted": models,
        "scaffold_model_selected": selected_model,
        "response_model": selected_response_model,
        "temperature_used": temperature_used,
        "timeout_seconds": timeout_seconds,
        "prompt_chars_original": len(prompt_text),
        "prompt_chars_used": len(prompt_text_for_model),
        "prompt_was_truncated": prompt_was_truncated,
        "scaffold_chars": len(scaffold_text or ""),
        "scaffold_excerpt": (scaffold_text or "")[:1200] if scaffold_text else None,
        "raw_response_excerpt": raw_response_text[:1200] if raw_response_text else None,
        "error": error,
        "error_body": error_body,
        "attempts": attempts,
    }
    return scaffold_text, debug


def critique_planning_scaffold(
    *,
    scaffold_text: str,
    idea_text: str,
    input_directives: Any,
    start_date: Any,
    end_date: Any,
) -> tuple[str | None, dict[str, Any]]:
    """
    Critique a planning scaffold for problems before revision.

    Checks timings, constraint violations, lifestyle mismatches, day density,
    must-include completeness, and wake-time alignment.

    Returns critique text ending with VERDICT: needs_revision or VERDICT: approved,
    plus debug metadata. Fails open — caller falls back to original scaffold on error.
    """
    from models import InputDirectives as _InputDirectives

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None, {"error": "OPENROUTER_API_KEY is not set."}

    primary_model = os.environ.get("OPENROUTER_CRITIQUE_MODEL", "").strip()
    if not primary_model:
        primary_model = os.environ.get("OPENROUTER_COT_MODEL", "").strip()
    if not primary_model:
        primary_model = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()
    models = _resolve_scaffold_models(primary_model)

    timeout_seconds_raw = os.environ.get("OPENROUTER_CRITIQUE_TIMEOUT_SECONDS", "20")
    try:
        timeout_seconds = float(timeout_seconds_raw)
    except ValueError:
        timeout_seconds = 20.0
    timeout_seconds = max(5.0, min(timeout_seconds, 60.0))

    # Build a compact directives summary for the critique prompt
    directives_lines: list[str] = []
    if isinstance(input_directives, _InputDirectives):
        if input_directives.hard_constraints:
            directives_lines.append(f"Hard constraints: {'; '.join(input_directives.hard_constraints[:6])}")
        if input_directives.avoid:
            directives_lines.append(f"Avoid: {'; '.join(input_directives.avoid[:6])}")
        if input_directives.must_include:
            directives_lines.append(f"Must include: {'; '.join(input_directives.must_include[:6])}")
        if input_directives.pace:
            directives_lines.append(f"Pace: {input_directives.pace}")
        if input_directives.wake_time_pref:
            directives_lines.append(f"Wake time: {input_directives.wake_time_pref}")
        if input_directives.travel_party:
            directives_lines.append(f"Travel party: {'; '.join(input_directives.travel_party)}")
        if input_directives.dietary:
            directives_lines.append(f"Dietary: {'; '.join(input_directives.dietary)}")
        if input_directives.fitness_level:
            directives_lines.append(f"Fitness level: {input_directives.fitness_level}")
        if input_directives.mobility_mode:
            directives_lines.append(f"Mobility: {input_directives.mobility_mode}")
    directives_summary = "\n".join(directives_lines) if directives_lines else "None provided."

    date_range = ""
    if start_date and end_date:
        date_range = f"{start_date} to {end_date}"
    elif start_date:
        date_range = f"from {start_date}"

    user_prompt = (
        "Review this draft travel itinerary scaffold for quality problems. "
        "Be concise and specific. Flag only real issues.\n\n"
        f"User request: {idea_text[:600]}\n\n"
        f"Trip dates: {date_range or 'not specified'}\n\n"
        f"Traveller directives:\n{directives_summary}\n\n"
        "Draft scaffold to review:\n"
        f"{scaffold_text[:3000]}\n\n"
        "Check for:\n"
        "1. TIMING: Activities obviously too short (museum <60min, dining <45min) or too long\n"
        "2. CONSTRAINTS: Any hard constraint or avoid item violated\n"
        "3. LIFESTYLE: Activities inconsistent with travel_party, dietary, or fitness_level\n"
        "4. DENSITY: Sparse days or overpacked days relative to stated pace\n"
        "5. MUST-INCLUDE: All must_include items present\n"
        "6. WAKE TIME: First activity consistent with wake_time_pref\n\n"
        "Format: bullet points, each starting with the category name. "
        "Be specific (e.g. 'Day 2: museum visit at 20 min is too short'). "
        "If no real issues found, say so.\n\n"
        "End your response with exactly one of:\n"
        "VERDICT: needs_revision\n"
        "VERDICT: approved"
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    critique_text: str | None = None
    error: str | None = None
    attempts: list[dict[str, Any]] = []

    with httpx.Client(timeout=timeout_seconds) as client:
        for model in models:
            attempt: dict[str, Any] = {"model": model}
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a meticulous travel plan reviewer. "
                            "Identify specific, actionable problems with draft itineraries. "
                            "Be concise — bullet points only. No explanations beyond what is needed."
                        ),
                    },
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            }
            try:
                data = _post_openrouter(client=client, payload=payload, headers=headers, purpose="critique")
                raw_text = _message_content_to_text(data)
                candidate = _sanitize_planning_scaffold(raw_text)
                attempt["status"] = "ok"
                attempt["response_model"] = data.get("model")
                attempt["critique_chars"] = len(candidate)
                attempts.append(attempt)
                if candidate:
                    critique_text = candidate
                    break
                error = "Critique model returned empty text."
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:1500]
                error = f"HTTP {exc.response.status_code} (model={model})"
                attempt["status"] = "http_error"
                attempt["http_status"] = exc.response.status_code
                attempt["error_body_excerpt"] = detail
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                attempt["status"] = "exception"
                attempt["error"] = str(exc)
                attempts.append(attempt)

    debug: dict[str, Any] = {
        "critique_model_requested": primary_model,
        "timeout_seconds": timeout_seconds,
        "critique_chars": len(critique_text or ""),
        "error": error,
        "attempts": attempts,
    }
    return critique_text, debug


def revise_planning_scaffold(
    *,
    original_scaffold: str,
    critique_text: str,
    idea_text: str,
    input_directives: Any,
    start_date: Any,
    end_date: Any,
) -> tuple[str | None, dict[str, Any]]:
    """
    Produce a revised scaffold that addresses critique issues.

    Returns revised text plus debug metadata. Fails open — caller uses
    original scaffold if this returns None or raises.
    """
    from models import InputDirectives as _InputDirectives

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return None, {"error": "OPENROUTER_API_KEY is not set."}

    primary_model = os.environ.get("OPENROUTER_CRITIQUE_MODEL", "").strip()
    if not primary_model:
        primary_model = os.environ.get("OPENROUTER_COT_MODEL", "").strip()
    if not primary_model:
        primary_model = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip()
    models = _resolve_scaffold_models(primary_model)

    timeout_seconds_raw = os.environ.get("OPENROUTER_CRITIQUE_TIMEOUT_SECONDS", "20")
    try:
        timeout_seconds = float(timeout_seconds_raw)
    except ValueError:
        timeout_seconds = 20.0
    timeout_seconds = max(5.0, min(timeout_seconds, 90.0))

    date_range = ""
    if start_date and end_date:
        date_range = f"{start_date} to {end_date}"

    user_prompt = (
        "Revise this travel itinerary scaffold to fix the identified problems.\n\n"
        f"User request: {idea_text[:600]}\n\n"
        f"Trip dates: {date_range or 'not specified'}\n\n"
        "Original scaffold:\n"
        f"{original_scaffold[:2600]}\n\n"
        "Problems to fix:\n"
        f"{critique_text[:1200]}\n\n"
        "Output requirements:\n"
        "- Plain text only.\n"
        "- Produce a complete revised scaffold addressing each flagged issue.\n"
        "- Keep the same day-by-day structure.\n"
        "- Fix timing durations, constraint violations, lifestyle mismatches, and density issues.\n"
        "- If must-include items were missing, add them to appropriate days.\n"
        "- Adjust first-activity timing if wake_time issue was flagged.\n"
        "- Do not add new activities beyond what is needed to fix the issues."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    revised_text: str | None = None
    error: str | None = None
    attempts: list[dict[str, Any]] = []

    output_max_chars = _env_int(
        "OPENROUTER_COT_OUTPUT_MAX_CHARS",
        2400,
        min_value=300,
        max_value=8000,
    )

    with httpx.Client(timeout=timeout_seconds) as client:
        for model in models:
            attempt: dict[str, Any] = {"model": model}
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": PLANNING_SCAFFOLD_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            }
            try:
                data = _post_openrouter(client=client, payload=payload, headers=headers, purpose="revise")
                raw_text = _message_content_to_text(data)
                candidate = _sanitize_planning_scaffold(raw_text)
                attempt["status"] = "ok"
                attempt["response_model"] = data.get("model")
                attempt["revised_chars"] = len(candidate)
                attempts.append(attempt)
                if candidate:
                    revised_text = candidate[:output_max_chars]
                    break
                error = "Revise model returned empty text."
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:1500]
                error = f"HTTP {exc.response.status_code} (model={model})"
                attempt["status"] = "http_error"
                attempt["http_status"] = exc.response.status_code
                attempt["error_body_excerpt"] = detail
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                attempt["status"] = "exception"
                attempt["error"] = str(exc)
                attempts.append(attempt)

    debug: dict[str, Any] = {
        "revise_model_requested": primary_model,
        "timeout_seconds": timeout_seconds,
        "revised_chars": len(revised_text or ""),
        "error": error,
        "attempts": attempts,
    }
    return revised_text, debug


def build_web_queries(
    *,
    idea_text: str,
    trip_location: str | None,
    hard_constraints: list[str],
    soft_preferences: list[str],
    must_include: list[str],
    avoid: list[str],
    max_queries: int,
) -> tuple[list[str], dict[str, Any]]:
    """
    Build targeted web-search queries using a lightweight model.

    Returns sanitized queries and debug metadata. This path is free-model friendly:
    it avoids tool_choice and uses strict-JSON text output parsing.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return [], {"error": "OPENROUTER_API_KEY is not set."}

    primary_model = os.environ.get("OPENROUTER_QUERY_BUILDER_MODEL", "").strip()
    if not primary_model:
        primary_model = DEFAULT_OPENROUTER_QUERY_BUILDER_MODEL
    models = _resolve_query_builder_models(primary_model)

    timeout_seconds_raw = os.environ.get("OPENROUTER_QUERY_BUILDER_TIMEOUT_SECONDS", "18")
    try:
        timeout_seconds = float(timeout_seconds_raw)
    except ValueError:
        timeout_seconds = 18.0
    timeout_seconds = max(5.0, min(timeout_seconds, 60.0))

    idea_max_chars = _env_int(
        "OPENROUTER_QUERY_BUILDER_IDEA_MAX_CHARS",
        1200,
        min_value=200,
        max_value=12000,
    )
    idea_text_normalized = " ".join(idea_text.split()).strip()
    idea_text_for_prompt = idea_text_normalized[:idea_max_chars]
    idea_was_truncated = len(idea_text_for_prompt) < len(idea_text_normalized)

    directives_blob = {
        "hard_constraints": hard_constraints[:8],
        "soft_preferences": soft_preferences[:8],
        "must_include": must_include[:8],
        "avoid": avoid[:8],
    }
    user_prompt = (
        "Create high-quality web queries for travel grounding.\n"
        f"Trip location: {trip_location or 'unknown'}\n"
        f"Idea text: {idea_text_for_prompt}\n"
        f"Directives: {json.dumps(directives_blob, ensure_ascii=True)}\n"
        "Requirements:\n"
        f"- Return 4 to {max_queries} queries.\n"
        "- Ensure the query is contextualised and relevant to location.\n"
        "- Use search-string style keywords, not full-sentence questions.\n"
        "- When named places/activities exist, make queries entity-specific.\n"
        "- If both places and activities exist, include coverage across both types.\n"
        "- If safety constraints exist (for example avoid night driving), include one constraint-specific query.\n"
        "- Include at least one user-POV accessibility or ease query when relevant.\n"
        "- Prefer official and operational queries: official site, opening hours, booking/tickets, access/logistics.\n"
        "- Include at least one constraint-driven query if constraints/avoid terms exist.\n"
        "- Keep each query short and precise (roughly 4-10 words).\n"
        "- Avoid leading question words like what/how/where/when/why.\n"
        "- Output strict JSON only under the provided schema.\n"
        "- Do not include any additional text outside of the JSON object."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    raw_text = ""
    parsed_json: dict[str, Any] | None = None
    error: str | None = None
    error_body: str | None = None
    queries: list[str] = []
    selected_model: str | None = None
    selected_response_model: str | None = None
    temperature_used: float | None = None
    attempts: list[dict[str, Any]] = []

    with httpx.Client(timeout=timeout_seconds) as client:
        for model in models:
            for include_temperature in (True, False):
                attempt: dict[str, Any] = {
                    "model": model,
                    "include_temperature": include_temperature,
                }
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": QUERY_BUILDER_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                }
                if include_temperature:
                    payload["temperature"] = 0.1

                try:
                    data = _post_openrouter(client=client, payload=payload, headers=headers, purpose="query_builder")
                    raw_text = _message_content_to_text(data)
                    parsed_json = _extract_first_json_object(raw_text)
                    raw_queries = parsed_json.get("queries", [])
                    if isinstance(raw_queries, list):
                        for item in raw_queries:
                            if isinstance(item, dict):
                                candidate = _sanitize_query(str(item.get("q", "")))
                            else:
                                candidate = _sanitize_query(str(item))
                            if candidate:
                                queries.append(candidate)
                    sanitized_candidate = _dedupe_keep_order(queries)[:max(1, max_queries)]
                    attempt["status"] = "ok"
                    attempt["sanitized_query_count"] = len(sanitized_candidate)
                    attempt["response_model"] = data.get("model")
                    attempts.append(attempt)
                    if sanitized_candidate:
                        selected_model = model
                        selected_response_model = str(data.get("model", ""))
                        temperature_used = 0.1 if include_temperature else None
                        break
                    error = "Query builder returned no usable queries."
                except httpx.HTTPStatusError as exc:
                    detail = exc.response.text[:1500]
                    error = (
                        f"HTTP {exc.response.status_code} from OpenRouter "
                        f"(model={model}, include_temperature={include_temperature})"
                    )
                    error_body = detail
                    attempt["status"] = "http_error"
                    attempt["http_status"] = exc.response.status_code
                    attempt["error_body_excerpt"] = detail
                    attempts.append(attempt)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                    attempt["status"] = "exception"
                    attempt["error"] = str(exc)
                    attempts.append(attempt)

            if selected_model:
                break

    sanitized = _dedupe_keep_order(queries)[:max(1, max_queries)]
    debug = {
        "query_builder_model": selected_model or primary_model,
        "query_builder_models_attempted": models,
        "timeout_seconds": timeout_seconds,
        "idea_chars_original": len(idea_text_normalized),
        "idea_chars_used": len(idea_text_for_prompt),
        "idea_was_truncated": idea_was_truncated,
        "temperature_used": temperature_used,
        "response_model": selected_response_model,
        "raw_response_excerpt": raw_text[:1000] if raw_text else None,
        "parsed_query_count": len(queries),
        "sanitized_query_count": len(sanitized),
        "error": error,
        "error_body": error_body,
        "attempts": attempts,
    }
    if parsed_json is not None:
        debug["parsed_json"] = parsed_json

    return sanitized, debug


def _coerce_str_list(value: Any, *, max_items: int = 6, max_chars: int = 80) -> list[str]:
    if not isinstance(value, list):
        return []

    seen: set[str] = set()
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = f"{text[: max_chars - 1]}…"
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
        if len(normalized) >= max_items:
            break
    return normalized


def _coerce_media_signal(raw: Any) -> MediaSignal | None:
    if not isinstance(raw, dict):
        return None

    summary = str(raw.get("summary", "")).strip()
    if not summary:
        return None
    if len(summary) > 220:
        summary = f"{summary[:219]}…"

    source_filename = raw.get("source_filename")
    if source_filename is not None:
        source_filename = str(source_filename).strip() or None

    confidence_raw = raw.get("confidence", 0.55)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.55
    confidence = max(0.0, min(confidence, 1.0))

    return MediaSignal(
        source_filename=source_filename,
        summary=summary,
        location_cues=_coerce_str_list(raw.get("location_cues")),
        activity_hints=_coerce_str_list(raw.get("activity_hints")),
        vibe_tags=_coerce_str_list(raw.get("vibe_tags")),
        constraints=_coerce_str_list(raw.get("constraints")),
        confidence=confidence,
    )


def extract_media_context(
    *,
    idea_text: str,
    media_inputs: list[MediaInput],
) -> list[MediaSignal]:
    """
    Parse uploaded media into compact planning signals.

    This runs as a dedicated vision stage and returns concise structured facts.
    """
    if not media_inputs:
        return []

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is not set in environment or .env file")

    image_model = os.environ.get("OPENROUTER_IMAGE_MODEL", "").strip()
    if not image_model:
        image_model = DEFAULT_OPENROUTER_IMAGE_MODEL

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    user_content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": (
                "Extract planning-relevant visual context from uploaded media.\n"
                "Prioritize place cues, activity affordances, vibe, and constraints.\n"
                "User request for context:\n"
                f"{idea_text}"
            ),
        }
    ]
    for media in media_inputs:
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

    errors: list[str] = []
    with httpx.Client(timeout=60.0) as client:
        allow_unforced_retry = False
        for force_tool_choice in (True, False):
            if not force_tool_choice and not allow_unforced_retry:
                continue

            payload: dict[str, Any] = {
                "model": image_model,
                "messages": [
                    {"role": "system", "content": MEDIA_CONTEXT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "tools": [MEDIA_CONTEXT_TOOL],
            }
            if force_tool_choice:
                payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": "extract_media_context"},
                }

            try:
                data = _post_openrouter(client=client, payload=payload, headers=headers, purpose="media_context")
                arguments = _extract_tool_arguments(
                    data,
                    tool_name="extract_media_context",
                )
                raw_facts = arguments.get("visual_facts", [])
                if not isinstance(raw_facts, list):
                    return []
                signals: list[MediaSignal] = []
                for raw_item in raw_facts:
                    parsed = _coerce_media_signal(raw_item)
                    if parsed:
                        signals.append(parsed)
                return signals
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                errors.append(
                    "request failed "
                    f"(model={image_model}, force_tool_choice={force_tool_choice}, "
                    f"status={exc.response.status_code}): {detail}"
                )
                can_retry_compat = (
                    force_tool_choice
                    and _should_retry_without_forced_tool_choice(exc)
                )
                if can_retry_compat:
                    allow_unforced_retry = True
                    continue
                break
            except ValueError as exc:
                errors.append(
                    "response parse failed "
                    f"(model={image_model}, force_tool_choice={force_tool_choice}): {exc}"
                )
                break

    logger.warning(
        "OpenRouter media-context extraction failed; continuing without vision facts. Details: %s",
        " | ".join(errors[-3:]) if errors else "No attempt details available.",
    )
    return []
