"""
URL scraping helpers for user-provided web links.

This module fetches a URL, extracts the highest-signal page text,
then derives lightweight planning hints for the itinerary pipeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import re
import time
from urllib.parse import urlparse

import httpx

try:
    from scrapy import Selector
except Exception:  # pragma: no cover
    Selector = None

logger = logging.getLogger(__name__)


FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

# Sites that use JS-based bot challenges (Cloudflare Turnstile, DataDome, etc.)
# that cannot be bypassed with header spoofing alone.
JS_CHALLENGE_DOMAINS = frozenset(
    {
        "www.tripadvisor.com",
        "tripadvisor.com",
        "www.booking.com",
        "booking.com",
        "www.airbnb.com",
        "airbnb.com",
        "www.expedia.com",
        "expedia.com",
        "www.hotels.com",
        "hotels.com",
        "www.viator.com",
        "viator.com",
        "www.getyourguide.com",
        "getyourguide.com",
    }
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MAIN_CONTENT_SELECTORS = (
    "main",
    "article",
    "[role='main']",
    ".post-content",
    ".entry-content",
    ".article-content",
    ".content",
    ".main-content",
)

TIME_KEYWORDS = (
    "best time",
    "opening hours",
    "open from",
    "morning",
    "afternoon",
    "evening",
    "sunset",
    "night",
    "weekday",
    "weekend",
    "season",
    "last entry",
)

CONSTRAINT_KEYWORDS = (
    "reservation",
    "book",
    "booking",
    "ticket",
    "timed entry",
    "closed",
    "open",
    "requires",
    "must",
    "limit",
    "age",
    "ID required",
)

ACTIVITY_KEYWORDS = {
    "museum": "museum",
    "gallery": "gallery",
    "hike": "hiking",
    "trail": "hiking",
    "walk": "walking",
    "beach": "beach",
    "restaurant": "dining",
    "cafe": "dining",
    "bar": "nightlife",
    "ferry": "ferry",
    "tour": "tour",
    "zoo": "animals",
    "market": "shopping",
    "spa": "wellness",
    "hotel": "accommodation",
}

VIBE_KEYWORDS = (
    "romantic",
    "scenic",
    "family",
    "budget",
    "luxury",
    "adventure",
    "relaxing",
    "nightlife",
)

LOW_SIGNAL_TEXT_PATTERNS = (
    "cookie",
    "privacy policy",
    "terms of use",
    "all rights reserved",
    "newsletter",
    "subscribe",
    "enable javascript",
    "skip to content",
    "accept all",
    "sign up",
)

BOILERPLATE_NOISE_PATTERNS = (
    "skip to primary content",
    "jump to navigation",
    "main navigation",
    "table of contents",
    "related posts",
    "read more",
    "advertisement",
    "sponsored",
    "copyright",
    "powered by",
    "wp-block",
)

STYLE_NOISE_MARKERS = (
    "--wp--preset--",
    ":root{",
    "@media",
    "var(--",
    "font-family:",
    "line-height:",
    "letter-spacing:",
    "padding:",
    "margin:",
    "display:",
    "background:",
    "color:",
    "rgb(",
    "rgba(",
    "calc(",
)

URL_SUMMARY_SYSTEM_PROMPT = (
    "You compress scraped travel webpage text into operational planning facts. "
    "The input is split into PART_A and PART_B from the same page. "
    "Return strict JSON only with schema: "
    '{"merged_summary":"string","time_hints":["string"],"booking_constraints":["string"],'
    '"transport_notes":["string"],"must_know_facts":["string"]}. '
    "Rules: "
    "- Only use facts explicitly stated in the provided text — do not add, infer, or hallucinate any information. "
    "- Use exact place names as written in the source, never generic labels like 'golden temple' or 'famous shrine'. "
    "- time_hints: pair each named place with its specific opening hours and recommended visit window if mentioned "
    "(e.g. opens 09:00, arrive before 09:30 to avoid crowds'). "
    "- transport_notes: include specific bus/train line numbers, journey durations, and transfer points if mentioned. "
    "- must_know_facts: include (1) visit duration per named place if mentioned, "
    "(2) which named places are geographically close and should be grouped in one visit, "
    "(3) recommended visit order based on crowd timing or operational constraints from the source "
    "(e.g. 'visit X before Y because crowds peak at midday') — no day-by-day structure, sequencing logic only. "
    "Keep all fields concise and factual."
)


URL_SUMMARY_MAP_SYSTEM_PROMPT = (
    "You compress one CHUNK of a scraped travel webpage into operational planning facts. "
    "Return strict JSON only with schema: "
    '{"merged_summary":"string","time_hints":["string"],"booking_constraints":["string"],'
    '"transport_notes":["string"],"must_know_facts":["string"]}. '
    "Rules: "
    "- Only use facts explicitly present in this chunk — do not add, infer, or hallucinate any information. "
    "- Use exact place names as written in the text, not generic categories. "
    "- time_hints: named place + specific time or window if stated (e.g. 'Fushimi Inari: quieter after 18:00'). "
    "- transport_notes: specific routes, bus/train lines, durations, and transfer points if mentioned. "
    "- must_know_facts: visit durations per named place, geographic clustering signals, and ordering constraints "
    "if stated in the source. "
    "Keep all fields concise and factual."
)

URL_SUMMARY_REDUCE_SYSTEM_PROMPT = (
    "You merge multiple chunk-level travel summaries from the same webpage into one final result. "
    "Return strict JSON only with schema: "
    '{"merged_summary":"string","time_hints":["string"],"booking_constraints":["string"],'
    '"transport_notes":["string"],"must_know_facts":["string"]}. '
    "Rules: "
    "- Only include facts present in the chunk summaries — do not add, infer, or hallucinate any information. "
    "- Use exact place names from the source, never generic labels. "
    "- time_hints: specific named places with opening times and recommended visit windows from the source. "
    "- transport_notes: specific bus/train routes, line numbers, durations, and transfer logic from the source. "
    "- must_know_facts: consolidate into (1) visit duration per named place, "
    "(2) which named places cluster geographically and should be visited together, "
    "(3) recommended visit order based on crowd patterns or time constraints stated in the source "
    "(e.g. 'visit Arashiyama bamboo grove before 07:30, then pair with Tenryu-ji') — "
    "sequencing logic only, no day-by-day structure. "
    "Deduplicate overlapping points."
)


@dataclass(frozen=True)
class UrlScrapeResult:
    ok: bool
    source_url: str
    final_url: str
    host: str
    status_code: int | None
    error: str | None
    parser: str
    page_title: str | None
    meta_description: str | None
    content_excerpt: str | None
    parsed_text_preview: str | None
    raw_text_preview: str | None
    parsed_text_full: str | None
    raw_text_full: str | None
    llm_condensed_preview: str | None
    llm_condensed_full: str | None
    llm_summary_model: str | None
    llm_summary_error: str | None
    llm_summary_trace: dict[str, object] | None
    activity_hints: list[str]
    location_hints: list[str]
    vibe_tags: list[str]
    time_hints: list[str]
    constraints: list[str]
    time_hint_sentences: list[str]
    constraint_sentences: list[str]
    confidence: float


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        parsed = float(raw)
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_low_signal_text(value: str) -> bool:
    lowered = value.lower()
    if any(pattern in lowered for pattern in LOW_SIGNAL_TEXT_PATTERNS):
        return True
    if any(pattern in lowered for pattern in BOILERPLATE_NOISE_PATTERNS):
        return True
    if any(marker in lowered for marker in STYLE_NOISE_MARKERS):
        return True
    if len(value) >= 80 and (value.count("{") + value.count("}") + value.count(";")) >= 6:
        return True
    if re.search(r"[a-z_-]+\s*:\s*[^ ]+", lowered) and value.count(";") >= 4:
        return True
    if value.count("|") >= 2:
        return True
    if len(value.split()) < 4:
        return True
    if value.startswith("http://") or value.startswith("https://"):
        return True
    return False


def _alpha_ratio(value: str) -> float:
    if not value:
        return 0.0
    letters = sum(1 for ch in value if ch.isalpha())
    return letters / len(value)


def _preprocess_text_for_llm(text: str) -> tuple[str, dict[str, object]]:
    enabled = _env_bool("URL_SCRAPER_PREPROCESS_ENABLED", True)
    normalized = _normalize_space(text)
    if not normalized:
        return "", {"enabled": enabled, "reason": "empty_input"}
    if not enabled:
        return normalized, {
            "enabled": False,
            "input_chars": len(normalized),
            "output_chars": len(normalized),
            "removed_fragments": 0,
            "kept_fragments": 1,
        }

    max_fragments = _env_int(
        "URL_SCRAPER_PREPROCESS_MAX_FRAGMENTS",
        900,
        minimum=120,
        maximum=3000,
    )
    min_alpha_ratio = _env_float(
        "URL_SCRAPER_PREPROCESS_MIN_ALPHA_RATIO",
        0.56,
        minimum=0.35,
        maximum=0.9,
    )

    fragments = re.split(r"(?<=[.!?;])\s+|\s{2,}", normalized)
    kept: list[str] = []
    dropped_short = 0
    dropped_low_signal = 0
    dropped_low_alpha = 0
    dropped_long_token = 0

    for raw_fragment in fragments:
        fragment = _normalize_space(raw_fragment)
        if not fragment:
            continue
        if len(fragment) < 24:
            dropped_short += 1
            continue
        if _is_low_signal_text(fragment):
            dropped_low_signal += 1
            continue
        if len(fragment) > 42 and _alpha_ratio(fragment) < min_alpha_ratio:
            dropped_low_alpha += 1
            continue
        if any(len(token) >= 56 for token in fragment.split()):
            dropped_long_token += 1
            continue
        kept.append(fragment)
        if len(kept) >= max_fragments:
            break

    deduped = _dedupe_keep_order(kept)
    cleaned = _normalize_space(" ".join(deduped))

    fallback_used = False
    if not cleaned:
        cleaned = normalized
        fallback_used = True

    return cleaned, {
        "enabled": True,
        "input_chars": len(normalized),
        "output_chars": len(cleaned),
        "input_fragments": len([x for x in fragments if _normalize_space(x)]),
        "kept_fragments": len(deduped),
        "removed_fragments": dropped_short + dropped_low_signal + dropped_low_alpha + dropped_long_token,
        "dropped_short": dropped_short,
        "dropped_low_signal": dropped_low_signal,
        "dropped_low_alpha": dropped_low_alpha,
        "dropped_long_token": dropped_long_token,
        "fallback_used": fallback_used,
    }


def _extract_title(html: str, selector: Selector | None) -> str:
    if selector is not None:
        title = _normalize_space(" ".join(selector.xpath("//title/text()").getall()))
        if title:
            return title
    match = re.search(r"(?is)<title>(.*?)</title>", html)
    if not match:
        return ""
    return _normalize_space(match.group(1))


def _extract_meta_description(html: str, selector: Selector | None) -> str:
    if selector is not None:
        meta = selector.xpath(
            "//meta[translate(@name,'DESCRIPTION','description')='description']/@content"
        ).get()
        if not meta:
            meta = selector.xpath(
                "//meta[translate(@property,'OG:DESCRIPTION','og:description')='og:description']/@content"
            ).get()
        if meta:
            return _normalize_space(meta)

    patterns = [
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
        r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
        r'(?is)<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:description["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return _normalize_space(match.group(1))
    return ""


def _clean_text_chunks(chunks: list[str], *, max_items: int) -> list[str]:
    cleaned: list[str] = []
    for raw in chunks:
        item = _normalize_space(raw)
        if not item:
            continue
        if _is_low_signal_text(item):
            continue
        cleaned.append(item)
        if len(cleaned) >= max_items:
            break
    return _dedupe_keep_order(cleaned)


def _clean_text_chunks_relaxed(chunks: list[str], *, max_items: int) -> list[str]:
    cleaned: list[str] = []
    for raw in chunks:
        item = _normalize_space(raw)
        if not item:
            continue
        lowered = item.lower()
        if any(pattern in lowered for pattern in LOW_SIGNAL_TEXT_PATTERNS):
            continue
        if item.startswith("http://") or item.startswith("https://"):
            continue
        cleaned.append(item)
        if len(cleaned) >= max_items:
            break
    return _dedupe_keep_order(cleaned)


def _strip_html_text(html: str, *, max_chars: int | None) -> str:
    stripped = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    stripped = re.sub(r"(?is)<[^>]+>", " ", stripped)
    normalized = _normalize_space(stripped)
    if max_chars is None:
        return normalized
    return normalized[:max_chars]


def _message_content_to_text(data: dict[str, object]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice, dict) else {}
    if not isinstance(message, dict):
        return ""
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


def _extract_first_json_object(raw_text: str) -> dict[str, object]:
    text = raw_text.strip()
    if not text:
        raise ValueError("URL summary model returned empty response.")

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
        raise ValueError("URL summary model JSON root is not an object.")
    return parsed


def _resolve_url_summary_model() -> str:
    explicit = os.getenv("OPENROUTER_URL_SUMMARY_MODEL", "").strip()
    if explicit:
        return explicit
    dedicated_scraper_model = os.getenv("URL_SCRAPER_LLM_MODEL", "").strip()
    if dedicated_scraper_model:
        return dedicated_scraper_model
    fallback_model = os.getenv("OPENROUTER_FALLBACK_MODEL", "").strip()
    if fallback_model:
        return fallback_model
    # Fall back to the primary model rather than an unreliable free-tier nano model.
    primary_model = os.getenv("OPENROUTER_MODEL", "").strip()
    if primary_model:
        return primary_model
    return "qwen/qwen3-next-80b-a3b-instruct:free"


def _split_text_for_llm_summary(text: str, *, max_chars: int) -> tuple[str, str]:
    normalized = _normalize_space(text)
    if max_chars > 0 and len(normalized) > max_chars:
        normalized = normalized[:max_chars]

    midpoint = len(normalized) // 2
    split_pos = normalized.rfind(". ", 0, midpoint + 1)
    if split_pos < 200:
        split_pos = normalized.rfind(" ", 0, midpoint + 1)
    if split_pos < 200:
        split_pos = midpoint

    part_a = normalized[:split_pos].strip()
    part_b = normalized[split_pos:].strip()
    if not part_a:
        part_a = normalized[:midpoint].strip()
    if not part_b:
        part_b = normalized[midpoint:].strip()
    return part_a, part_b


def _safe_list_of_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = _normalize_space(item)
        if cleaned:
            result.append(cleaned)
    return result


def _build_condensed_text_from_json(
    parsed: dict[str, object],
    *,
    fallback_raw_text: str = "",
) -> str | None:
    lines: list[str] = []
    merged_summary = str(parsed.get("merged_summary") or "").strip()
    if merged_summary:
        lines.append(merged_summary)
    lines.extend(_safe_list_of_strings(parsed.get("time_hints"))[:4])
    lines.extend(_safe_list_of_strings(parsed.get("booking_constraints"))[:4])
    lines.extend(_safe_list_of_strings(parsed.get("transport_notes"))[:3])
    lines.extend(_safe_list_of_strings(parsed.get("must_know_facts"))[:6])

    condensed = _normalize_space(" ".join(lines))
    if not condensed and fallback_raw_text:
        condensed = _normalize_space(fallback_raw_text)
    return condensed or None


async def _call_url_summary_model_async(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: float,
    max_tokens: int,
    call_label: str | None = None,
) -> tuple[str | None, str | None, str | None, float]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }

    started_at = time.perf_counter()
    log_prompt_preview = _env_bool("URL_SCRAPER_LOG_PROMPT_PREVIEW", True)
    prompt_preview_chars = _env_int(
        "URL_SCRAPER_LOG_PROMPT_PREVIEW_CHARS",
        600,
        minimum=0,
        maximum=12000,
    )
    if call_label:
        logger.info(
            "URL summary call start: %s model=%s input_chars=%s max_tokens=%s timeout=%.1fs",
            call_label,
            model,
            len(user_prompt),
            max_tokens,
            timeout_seconds,
        )
        if log_prompt_preview:
            preview = user_prompt if prompt_preview_chars <= 0 else user_prompt[:prompt_preview_chars]
            logger.info("URL summary call input preview: %s :: %s", call_label, preview)
    try:
        response = await client.post(
            OPENROUTER_URL,
            json=payload,
            headers=headers,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        response_model = str(data.get("model") or model)
        raw_text = _message_content_to_text(data)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if call_label:
            logger.info(
                "URL summary call done: %s model=%s elapsed_ms=%.2f output_chars=%s",
                call_label,
                response_model,
                elapsed_ms,
                len(raw_text or ""),
            )
        if not raw_text.strip():
            # Surface the real reason from the API response so it's debuggable.
            finish_reason = None
            error_field = data.get("error")
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                finish_reason = choices[0].get("finish_reason")
            detail = f"finish_reason={finish_reason!r}"
            if error_field:
                detail += f" api_error={error_field!r}"
            if call_label:
                logger.warning(
                    "URL summary empty response: %s model=%s %s",
                    call_label,
                    response_model,
                    detail,
                )
            return None, response_model, f"URL summary model returned empty response text ({detail})", elapsed_ms
        return raw_text, response_model, None, elapsed_ms
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        if call_label:
            logger.warning(
                "URL summary call failed: %s model=%s elapsed_ms=%.2f error=%s",
                call_label,
                model,
                elapsed_ms,
                exc,
            )
        return None, model, str(exc), elapsed_ms


def _call_url_summary_model(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: float,
    max_tokens: int,
    call_label: str | None = None,
) -> tuple[str | None, str | None, str | None, float]:
    """Sync wrapper used only for the single-pass fallback path."""
    with httpx.Client(timeout=timeout_seconds) as client:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        started_at = time.perf_counter()
        log_prompt_preview = _env_bool("URL_SCRAPER_LOG_PROMPT_PREVIEW", True)
        prompt_preview_chars = _env_int(
            "URL_SCRAPER_LOG_PROMPT_PREVIEW_CHARS",
            600,
            minimum=0,
            maximum=12000,
        )
        if call_label:
            logger.info(
                "URL summary call start: %s model=%s input_chars=%s max_tokens=%s timeout=%.1fs",
                call_label,
                model,
                len(user_prompt),
                max_tokens,
                timeout_seconds,
            )
            if log_prompt_preview:
                preview = user_prompt if prompt_preview_chars <= 0 else user_prompt[:prompt_preview_chars]
                logger.info("URL summary call input preview: %s :: %s", call_label, preview)
        try:
            response = client.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            response_model = str(data.get("model") or model)
            raw_text = _message_content_to_text(data)
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            if call_label:
                logger.info(
                    "URL summary call done: %s model=%s elapsed_ms=%.2f output_chars=%s",
                    call_label,
                    response_model,
                    elapsed_ms,
                    len(raw_text or ""),
                )
            if not raw_text.strip():
                return None, response_model, "URL summary model returned empty response text", elapsed_ms
            return raw_text, response_model, None, elapsed_ms
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            if call_label:
                logger.warning(
                    "URL summary call failed: %s model=%s elapsed_ms=%.2f error=%s",
                    call_label,
                    model,
                    elapsed_ms,
                    exc,
                )
            return None, model, str(exc), elapsed_ms


def _split_text_for_map_reduce(
    text: str,
    *,
    chunk_chars: int,
    max_chunks: int,
) -> list[str]:
    normalized = _normalize_space(text)
    if not normalized:
        return []

    text_len = len(normalized)
    effective_chunk_chars = chunk_chars
    if text_len > chunk_chars * max_chunks:
        # Keep full text without dropping tail content by rebalancing chunk size.
        effective_chunk_chars = max(chunk_chars, (text_len + max_chunks - 1) // max_chunks)

    chunks: list[str] = []
    cursor = 0

    while cursor < text_len and len(chunks) < max_chunks:
        end = min(cursor + effective_chunk_chars, text_len)
        if end < text_len:
            sentence_break = normalized.rfind(". ", cursor, end)
            if sentence_break <= cursor + (effective_chunk_chars // 3):
                sentence_break = normalized.rfind(" ", cursor, end)
            if sentence_break > cursor:
                end = sentence_break + 1

        chunk = normalized[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        cursor = end

    if cursor < text_len:
        remainder = normalized[cursor:].strip()
        if remainder:
            if chunks:
                chunks[-1] = _normalize_space(f"{chunks[-1]} {remainder}")
            else:
                chunks.append(remainder)

    return chunks


async def _map_chunk_summary_async(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    source_url: str,
    page_title: str,
    chunk_index: int,
    chunk_text: str,
    timeout_seconds: float,
    max_tokens: int,
) -> tuple[dict[str, object] | None, str | None, str | None, float, int]:
    prompt = (
        f"URL: {source_url}\n"
        f"TITLE: {page_title or 'unknown'}\n"
        f"CHUNK_INDEX: {chunk_index}\n\n"
        "CHUNK_TEXT:\n"
        f"{chunk_text}\n"
    )

    raw_text, response_model, error, elapsed_ms = await _call_url_summary_model_async(
        client=client,
        api_key=api_key,
        model=model,
        system_prompt=URL_SUMMARY_MAP_SYSTEM_PROMPT,
        user_prompt=prompt,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        call_label=f"map_chunk_{chunk_index}",
    )
    if error:
        return None, response_model, error, elapsed_ms, 0
    if raw_text is None:
        return None, response_model, "URL summary model returned no map output", elapsed_ms, 0

    try:
        parsed = _extract_first_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001
        return None, response_model, f"map_parse_error: {exc}", elapsed_ms, len(raw_text)

    return parsed, response_model, None, elapsed_ms, len(raw_text)


def _condense_with_llm(
    *,
    source_url: str,
    page_title: str,
    text: str,
) -> tuple[str | None, str | None, str | None, dict[str, object] | None]:
    if not _env_bool("URL_SCRAPER_LLM_SUMMARY_ENABLED", True):
        return (
            None,
            None,
            None,
            {
                "enabled": False,
                "mode": "disabled",
                "calls_total": 0,
                "models_used": [],
                "reason": "URL_SCRAPER_LLM_SUMMARY_ENABLED=false",
            },
        )
    if len(text.strip()) < 320:
        return (
            None,
            None,
            None,
            {
                "enabled": True,
                "mode": "skipped_short_input",
                "calls_total": 0,
                "models_used": [],
                "reason": "input_too_short",
            },
        )

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return (
            None,
            None,
            "OPENROUTER_API_KEY not set",
            {
                "enabled": True,
                "mode": "config_error",
                "calls_total": 0,
                "models_used": [],
                "reason": "OPENROUTER_API_KEY not set",
            },
        )

    model = _resolve_url_summary_model()
    map_reduce_enabled = _env_bool("URL_SCRAPER_LLM_MAP_REDUCE_ENABLED", True)
    map_chunk_chars = _env_int(
        "URL_SCRAPER_LLM_MAP_CHUNK_CHARS",
        2800,
        minimum=800,
        maximum=14000,
    )
    map_max_chunks = _env_int(
        "URL_SCRAPER_LLM_MAP_MAX_CHUNKS",
        4,
        minimum=2,
        maximum=24,
    )
    map_workers = _env_int(
        "URL_SCRAPER_LLM_MAP_WORKERS",
        4,
        minimum=1,
        maximum=8,
    )
    map_timeout_seconds = _env_float(
        "URL_SCRAPER_LLM_MAP_TIMEOUT_SECONDS",
        30.0,
        minimum=4.0,
        maximum=120.0,
    )
    reduce_timeout_seconds = _env_float(
        "URL_SCRAPER_LLM_REDUCE_TIMEOUT_SECONDS",
        30.0,
        minimum=4.0,
        maximum=120.0,
    )
    single_pass_timeout_seconds = _env_float(
        "URL_SCRAPER_LLM_TIMEOUT_SECONDS",
        30.0,
        minimum=4.0,
        maximum=120.0,
    )
    input_max_chars = _env_int(
        "URL_SCRAPER_LLM_INPUT_MAX_CHARS",
        0,
        minimum=0,
        maximum=90000,
    )
    output_max_chars = _env_int(
        "URL_SCRAPER_LLM_OUTPUT_MAX_CHARS",
        1800,
        minimum=0,
        maximum=6000,
    )
    map_max_tokens = _env_int(
        "URL_SCRAPER_LLM_MAP_MAX_TOKENS",
        1024,
        minimum=80,
        maximum=4000,
    )
    reduce_max_tokens = _env_int(
        "URL_SCRAPER_LLM_REDUCE_MAX_TOKENS",
        1024,
        minimum=100,
        maximum=4000,
    )
    single_pass_max_tokens = _env_int(
        "URL_SCRAPER_LLM_SINGLE_PASS_MAX_TOKENS",
        1024,
        minimum=120,
        maximum=4000,
    )

    mode = "single_pass"
    fallback_path: str | None = None
    call_models: list[str] = []
    map_chunks_total = 0
    map_chunks_succeeded = 0
    map_chunks_failed = 0
    reduce_called = False
    reduce_succeeded = False
    map_call_stats: list[dict[str, object]] = []
    reduce_call_stat: dict[str, object] | None = None
    single_pass_call_stat: dict[str, object] | None = None

    input_preview_chars = _env_int(
        "URL_SCRAPER_LLM_DEBUG_INPUT_PREVIEW_CHARS",
        220,
        minimum=40,
        maximum=1200,
    )

    input_chars_original = 0
    prompt_was_truncated = False
    preprocess_stats: dict[str, object] | None = None

    def _trace() -> dict[str, object]:
        return {
            "enabled": True,
            "mode": mode,
            "calls_total": len(call_models),
            "models_used": _dedupe_keep_order(call_models),
            "map_chunks_total": map_chunks_total,
            "map_chunks_succeeded": map_chunks_succeeded,
            "map_chunks_failed": map_chunks_failed,
            "reduce_called": reduce_called,
            "reduce_succeeded": reduce_succeeded,
            "fallback_path": fallback_path,
            "input_chars_original": input_chars_original,
            "input_chars_after_budget": len(normalized_text),
            "prompt_was_truncated": prompt_was_truncated,
            "preprocess": preprocess_stats,
            "config": {
                "map_reduce_enabled": map_reduce_enabled,
                "map_chunk_chars": map_chunk_chars,
                "map_max_chunks": map_max_chunks,
                "map_workers": map_workers,
                "map_timeout_seconds": map_timeout_seconds,
                "reduce_timeout_seconds": reduce_timeout_seconds,
                "single_pass_timeout_seconds": single_pass_timeout_seconds,
                "input_max_chars": input_max_chars,
                "output_max_chars": output_max_chars,
                "map_max_tokens": map_max_tokens,
                "reduce_max_tokens": reduce_max_tokens,
                "single_pass_max_tokens": single_pass_max_tokens,
            },
            "map_calls": map_call_stats,
            "reduce_call": reduce_call_stat,
            "single_pass_call": single_pass_call_stat,
        }

    normalized_text = _normalize_space(text)
    if not normalized_text:
        return None, model, "URL summary input text is empty after normalization", _trace()

    input_chars_original = len(normalized_text)
    normalized_text, preprocess_stats = _preprocess_text_for_llm(normalized_text)
    if not normalized_text:
        return None, model, "URL summary input text is empty after preprocessing", _trace()
    if input_max_chars > 0:
        prompt_was_truncated = len(normalized_text) > input_max_chars
        normalized_text = normalized_text[:input_max_chars]

    def _apply_output_limit(value: str) -> str:
        if output_max_chars <= 0:
            return value
        return value[:output_max_chars]

    fallback_warnings: list[str] = []

    if map_reduce_enabled:
        chunks = _split_text_for_map_reduce(
            normalized_text,
            chunk_chars=map_chunk_chars,
            max_chunks=map_max_chunks,
        )
        if len(chunks) >= 2:
            mode = "map_reduce"
            map_chunks_total = len(chunks)
            chunk_results: list[tuple[int, dict[str, object]]] = []
            chunk_errors: list[str] = []
            chunk_models: list[str] = []

            logger.info(
                "map_reduce start: url=%s chunks=%s workers=%s model=%s",
                source_url,
                len(chunks),
                map_workers,
                model,
            )

            async def _run_map_phase_async(
                chunk_list: list[str],
            ) -> list[tuple[int, str, object]]:
                """Fire all chunk LLM calls concurrently over a shared AsyncClient."""
                sem = asyncio.Semaphore(map_workers)

                async def _bounded_chunk(idx: int, text: str) -> tuple[int, str, object]:
                    async with sem:
                        result = await _map_chunk_summary_async(
                            client=client,
                            api_key=api_key,
                            model=model,
                            source_url=source_url,
                            page_title=page_title,
                            chunk_index=idx,
                            chunk_text=text,
                            timeout_seconds=map_timeout_seconds,
                            max_tokens=map_max_tokens,
                        )
                    return idx, text, result

                async with httpx.AsyncClient() as client:
                    tasks = [
                        _bounded_chunk(idx, text)
                        for idx, text in enumerate(chunk_list, start=1)
                    ]
                    return await asyncio.gather(*tasks, return_exceptions=True)

            map_results = asyncio.run(_run_map_phase_async(chunks))

            for chunk_index, chunk_text_local, raw_result in map_results:
                if isinstance(raw_result, BaseException):
                    exc = raw_result
                    chunk_errors.append(f"chunk_{chunk_index}: {exc}")
                    map_call_stats.append(
                        {
                            "chunk_index": chunk_index,
                            "status": "future_error",
                            "error": str(exc),
                            "input_chars": len(chunk_text_local),
                            "input_preview": chunk_text_local[:input_preview_chars],
                        }
                    )
                    continue

                (
                    parsed_chunk,
                    response_model,
                    chunk_error,
                    chunk_elapsed_ms,
                    chunk_output_chars,
                ) = raw_result

                call_stat: dict[str, object] = {
                    "chunk_index": chunk_index,
                    "status": "ok" if not chunk_error else "error",
                    "model": response_model,
                    "elapsed_ms": round(chunk_elapsed_ms, 2),
                    "input_chars": len(chunk_text_local),
                    "output_chars": chunk_output_chars,
                    "input_preview": chunk_text_local[:input_preview_chars],
                }
                if chunk_error:
                    call_stat["error"] = chunk_error
                map_call_stats.append(call_stat)

                if response_model:
                    chunk_models.append(response_model)
                    call_models.append(response_model)
                if chunk_error:
                    chunk_errors.append(f"chunk_{chunk_index}: {chunk_error}")
                    logger.warning("map_chunk_%s failed: %s", chunk_index, chunk_error)
                    continue
                if parsed_chunk:
                    chunk_results.append((chunk_index, parsed_chunk))

            map_chunks_succeeded = len(chunk_results)
            map_chunks_failed = len(chunk_errors)
            logger.info(
                "map_reduce map done: url=%s succeeded=%s failed=%s",
                source_url,
                map_chunks_succeeded,
                map_chunks_failed,
            )

            if chunk_results:
                chunk_results.sort(key=lambda item: item[0])
                reduce_payload = [
                    {"chunk_index": chunk_index, "summary": parsed_chunk}
                    for chunk_index, parsed_chunk in chunk_results
                ]
                reduce_prompt = (
                    f"URL: {source_url}\n"
                    f"TITLE: {page_title or 'unknown'}\n\n"
                    "CHUNK_SUMMARIES_JSON:\n"
                    f"{json.dumps(reduce_payload, ensure_ascii=False)}\n"
                )
                reduce_called = True
                (
                    reduce_raw_text,
                    reduce_model,
                    reduce_error,
                    reduce_elapsed_ms,
                ) = _call_url_summary_model(
                    api_key=api_key,
                    model=model,
                    system_prompt=URL_SUMMARY_REDUCE_SYSTEM_PROMPT,
                    user_prompt=reduce_prompt,
                    timeout_seconds=reduce_timeout_seconds,
                    max_tokens=reduce_max_tokens,
                    call_label="reduce",
                )
                reduce_call_stat = {
                    "status": "ok" if not reduce_error else "error",
                    "model": reduce_model,
                    "elapsed_ms": round(reduce_elapsed_ms, 2),
                    "input_chars": len(reduce_prompt),
                    "output_chars": len(reduce_raw_text or ""),
                    "input_preview": reduce_prompt[:input_preview_chars],
                }
                if reduce_error:
                    reduce_call_stat["error"] = reduce_error
                if reduce_model:
                    call_models.append(reduce_model)

                map_reduce_warnings: list[str] = []
                if chunk_errors:
                    map_reduce_warnings.append(
                        f"map_reduce_partial: {len(chunk_errors)}/{len(chunks)} chunk(s) failed"
                    )

                if reduce_error:
                    map_reduce_warnings.append(f"reduce_error: {reduce_error}")
                elif reduce_raw_text is not None:
                    try:
                        reduce_parsed = _extract_first_json_object(reduce_raw_text)
                        reduced_text = _build_condensed_text_from_json(
                            reduce_parsed,
                            fallback_raw_text=reduce_raw_text,
                        )
                        if reduced_text:
                            reduce_succeeded = True
                            return (
                                _apply_output_limit(reduced_text),
                                reduce_model or (chunk_models[0] if chunk_models else model),
                                " | ".join(map_reduce_warnings) if map_reduce_warnings else None,
                                _trace(),
                            )
                        map_reduce_warnings.append("reduce_error: empty condensed text")
                    except Exception as exc:  # noqa: BLE001
                        map_reduce_warnings.append(f"reduce_parse_error: {exc}")

                map_lines: list[str] = []
                for _, parsed_chunk in chunk_results:
                    partial = _build_condensed_text_from_json(parsed_chunk)
                    if partial:
                        map_lines.append(partial)
                map_concat_text = _normalize_space(" ".join(map_lines))
                if map_concat_text:
                    map_reduce_warnings.append("reduce_fallback: used map concat")
                    fallback_path = "map_concat"
                    return (
                        _apply_output_limit(map_concat_text),
                        chunk_models[0] if chunk_models else model,
                        " | ".join(map_reduce_warnings),
                        _trace(),
                    )

                fallback_warnings.append("map_reduce_failed: map produced no usable condensed text")
            elif chunk_errors:
                fallback_warnings.append(
                    f"map_reduce_failed: {len(chunk_errors)}/{len(chunks)} chunk(s) failed"
                )
            mode = "map_reduce_then_single_pass"
            fallback_path = "single_pass_after_map_reduce"

    part_a, part_b = _split_text_for_llm_summary(normalized_text, max_chars=0)
    single_prompt = (
        f"URL: {source_url}\n"
        f"TITLE: {page_title or 'unknown'}\n\n"
        "PART_A:\n"
        f"{part_a}\n\n"
        "PART_B:\n"
        f"{part_b}\n"
    )
    raw_text, response_model, error, single_elapsed_ms = _call_url_summary_model(
        api_key=api_key,
        model=model,
        system_prompt=URL_SUMMARY_SYSTEM_PROMPT,
        user_prompt=single_prompt,
        timeout_seconds=single_pass_timeout_seconds,
        max_tokens=single_pass_max_tokens,
        call_label="single_pass",
    )
    single_pass_call_stat = {
        "status": "ok" if not error else "error",
        "model": response_model,
        "elapsed_ms": round(single_elapsed_ms, 2),
        "input_chars": len(single_prompt),
        "output_chars": len(raw_text or ""),
        "input_preview": single_prompt[:input_preview_chars],
    }
    if error:
        single_pass_call_stat["error"] = error
    if response_model:
        call_models.append(response_model)
    def _fail(msg: str) -> tuple[None, str | None, str, dict]:
        prefix = " | ".join(fallback_warnings)
        full_msg = f"{prefix} | {msg}" if prefix else msg
        return None, response_model, full_msg, _trace()

    if error:
        return _fail(f"single_pass_error: {error}")
    if raw_text is None:
        return _fail("single_pass_error: no response text")

    try:
        parsed = _extract_first_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"single_pass_parse_error: {exc}")

    condensed = _build_condensed_text_from_json(parsed, fallback_raw_text=raw_text)
    if not condensed:
        return _fail("single_pass_error: empty condensed text")

    warning = " | ".join(fallback_warnings) if fallback_warnings else None
    return _apply_output_limit(condensed), response_model, warning, _trace()


def _extract_main_text(html: str, selector: Selector | None, *, max_chars: int) -> tuple[str, str]:
    if selector is None:
        return (_strip_html_text(html, max_chars=max_chars), "regex")

    for css_selector in MAIN_CONTENT_SELECTORS:
        blocks = selector.css(css_selector)
        if not blocks:
            continue
        for block in blocks[:3]:
            chunks = block.xpath(
                ".//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]"
            ).getall()
            cleaned = _clean_text_chunks(chunks, max_items=500)
            if not cleaned:
                continue
            candidate = _normalize_space(" ".join(cleaned))
            if len(candidate) >= 220:
                return (candidate[:max_chars], "scrapy_main")

    body_chunks = selector.xpath(
        "//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript or ancestor::svg)]"
    ).getall()
    cleaned_body = _clean_text_chunks(body_chunks, max_items=1400)
    strict_body = _normalize_space(" ".join(cleaned_body))
    if strict_body:
        return (strict_body[:max_chars], "scrapy_body")

    relaxed_body_chunks = _clean_text_chunks_relaxed(body_chunks, max_items=1800)
    relaxed_body = _normalize_space(" ".join(relaxed_body_chunks))
    if relaxed_body:
        return (relaxed_body[:max_chars], "scrapy_body_relaxed")

    return (_strip_html_text(html, max_chars=max_chars), "regex_fallback")


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    raw = re.split(r"(?<=[.!?])\s+|\s{2,}", text)
    result: list[str] = []
    for piece in raw:
        sentence = _normalize_space(piece)
        if len(sentence) < 28 or len(sentence) > 280:
            continue
        if _is_low_signal_text(sentence):
            continue
        result.append(sentence)
    return _dedupe_keep_order(result)


def _select_sentences(sentences: list[str], keywords: tuple[str, ...], limit: int) -> list[str]:
    selected: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            selected.append(sentence)
        if len(selected) >= limit:
            break
    return selected


def _sentence_score(sentence: str) -> int:
    lowered = sentence.lower()
    score = 0
    if any(keyword in lowered for keyword in TIME_KEYWORDS):
        score += 2
    if any(keyword in lowered for keyword in CONSTRAINT_KEYWORDS):
        score += 2
    if any(keyword in lowered for keyword in ACTIVITY_KEYWORDS):
        score += 1
    if any(tag in lowered for tag in VIBE_KEYWORDS):
        score += 1
    return score


def _build_content_excerpt(
    *,
    text: str,
    sentences: list[str],
    time_hint_sentences: list[str],
    constraint_sentences: list[str],
    max_chars: int,
) -> str:
    prioritized: list[str] = []
    prioritized.extend(time_hint_sentences)
    prioritized.extend(constraint_sentences)

    scored = sorted(sentences, key=_sentence_score, reverse=True)
    prioritized.extend(scored)

    deduped = _dedupe_keep_order(prioritized)
    if not deduped:
        return text[:max_chars]

    excerpt = ""
    for sentence in deduped:
        candidate = sentence if not excerpt else f"{excerpt} {sentence}"
        if len(candidate) > max_chars:
            break
        excerpt = candidate

    if excerpt:
        return excerpt
    return deduped[0][:max_chars]


def _extract_activity_hints(text_blob: str) -> list[str]:
    lowered = text_blob.lower()
    hints: list[str] = []
    for keyword, label in ACTIVITY_KEYWORDS.items():
        if keyword in lowered:
            hints.append(label)
    return _dedupe_keep_order(hints)


def _extract_vibe_tags(text_blob: str) -> list[str]:
    lowered = text_blob.lower()
    tags = [tag for tag in VIBE_KEYWORDS if tag in lowered]
    return _dedupe_keep_order(tags)


def _extract_location_hints(host: str, page_title: str, text: str) -> list[str]:
    hints: list[str] = []
    if host:
        hints.append(f"host:{host}")

    title_tokens = re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", page_title)
    hints.extend(title_tokens[:4])

    in_matches = re.findall(r"\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", text[:1800])
    hints.extend(in_matches[:4])

    return _dedupe_keep_order(hints)[:6]


def _truncate_hint(sentence: str, max_chars: int = 96) -> str:
    value = sentence.strip()
    if len(value) <= max_chars:
        return value
    return f"{value[: max_chars - 3].rstrip()}..."


def _score_confidence(
    *,
    page_title: str,
    meta_description: str,
    content_excerpt: str,
    time_hint_sentences: list[str],
    constraint_sentences: list[str],
    activity_hints: list[str],
    parser: str,
) -> float:
    score = 0.30
    if parser.startswith("scrapy"):
        score += 0.10
    if page_title:
        score += 0.15
    if meta_description:
        score += 0.10
    if len(content_excerpt) >= 220:
        score += 0.10
    if len(content_excerpt) >= 500:
        score += 0.05
    if time_hint_sentences:
        score += 0.10
    if constraint_sentences:
        score += 0.10
    if activity_hints:
        score += 0.10
    return max(0.2, min(score, 0.92))


REDDIT_HOSTS = frozenset({"www.reddit.com", "reddit.com", "old.reddit.com"})
REDDIT_JSON_HEADERS = {
    "User-Agent": "NovaSync/1.0 travel-planning-assistant",
    "Accept": "application/json",
}
REDDIT_MAX_COMMENTS = 12
REDDIT_MIN_COMMENT_SCORE = 2


def _is_reddit_url(host: str) -> bool:
    return host in REDDIT_HOSTS


def _rewrite_reddit_json_url(url: str) -> str:
    """Append .json to a Reddit URL, handling existing query strings."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path.endswith(".json"):
        path = path + ".json"
    return parsed._replace(path=path).geturl()


def _extract_reddit_post_text(data: list | dict) -> tuple[str, str]:
    """
    Parse Reddit JSON into (page_title, plain_text).
    Handles post pages (list of 2) and listing/search pages (single dict).
    """
    lines: list[str] = []
    title = ""

    if isinstance(data, list) and len(data) >= 1:
        # Post page: data[0] = post listing, data[1] = comment tree
        post_listing = data[0]
        post_children = (
            post_listing.get("data", {}).get("children", [])
            if isinstance(post_listing, dict)
            else []
        )
        if post_children:
            post = post_children[0].get("data", {})
            title = str(post.get("title", "")).strip()
            subreddit = str(post.get("subreddit_name_prefixed", "")).strip()
            selftext = str(post.get("selftext", "")).strip()
            if title:
                lines.append(f"[{subreddit}] {title}")
            if selftext and selftext not in {"[deleted]", "[removed]"}:
                lines.append(selftext)

        if len(data) >= 2:
            comment_listing = data[1]
            comment_children = (
                comment_listing.get("data", {}).get("children", [])
                if isinstance(comment_listing, dict)
                else []
            )
            scored: list[tuple[int, str]] = []
            for child in comment_children:
                if not isinstance(child, dict):
                    continue
                kind = child.get("kind")
                if kind != "t1":
                    continue
                body = str(child.get("data", {}).get("body", "")).strip()
                score = int(child.get("data", {}).get("score", 0))
                if body and body not in {"[deleted]", "[removed]"} and score >= REDDIT_MIN_COMMENT_SCORE:
                    scored.append((score, body))
            scored.sort(key=lambda item: item[0], reverse=True)
            for _, body in scored[:REDDIT_MAX_COMMENTS]:
                lines.append(f"[comment] {body}")

    elif isinstance(data, dict):
        # Subreddit listing or search results
        children = data.get("data", {}).get("children", [])
        for child in children[:10]:
            if not isinstance(child, dict):
                continue
            post = child.get("data", {})
            post_title = str(post.get("title", "")).strip()
            selftext = str(post.get("selftext", "")).strip()
            if post_title:
                lines.append(post_title)
            if selftext and selftext not in {"[deleted]", "[removed]"}:
                lines.append(selftext)

    return title, "\n\n".join(lines)


def _scrape_reddit_url(
    url: str,
    *,
    host: str,
    timeout_seconds: float,
    max_excerpt_chars: int,
    include_full_text: bool,
) -> UrlScrapeResult:
    """Fetch a Reddit URL via the public .json endpoint."""
    json_url = _rewrite_reddit_json_url(url)
    status_code: int | None = None

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            response = client.get(json_url, headers=REDDIT_JSON_HEADERS)
            status_code = response.status_code
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        return _empty_failure_result(url=url, host=host, status_code=status_code, error=str(exc))

    page_title, main_text = _extract_reddit_post_text(data)
    if not main_text:
        return _empty_failure_result(
            url=url,
            host=host,
            status_code=status_code,
            final_url=json_url,
            parser="reddit_json",
            error="reddit_json_empty_content",
        )

    llm_condensed_text, llm_summary_model, llm_summary_error, llm_summary_trace = _condense_with_llm(
        source_url=json_url,
        page_title=page_title,
        text=main_text,
    )
    planning_text = llm_condensed_text or main_text

    sentences = _split_sentences(planning_text)
    time_hint_sentences = _select_sentences(sentences, TIME_KEYWORDS, limit=4)
    constraint_sentences = _select_sentences(sentences, CONSTRAINT_KEYWORDS, limit=4)
    content_excerpt = _build_content_excerpt(
        text=planning_text,
        sentences=sentences,
        time_hint_sentences=time_hint_sentences,
        constraint_sentences=constraint_sentences,
        max_chars=max_excerpt_chars,
    )

    text_blob = " ".join([page_title, content_excerpt]).strip()
    activity_hints = _extract_activity_hints(text_blob)
    vibe_tags = _extract_vibe_tags(text_blob)
    location_hints = _extract_location_hints(host, page_title, content_excerpt or planning_text)
    time_hints = [_truncate_hint(s) for s in time_hint_sentences[:3]]
    constraints = [_truncate_hint(s) for s in constraint_sentences[:3]]
    confidence = _score_confidence(
        page_title=page_title,
        meta_description="",
        content_excerpt=content_excerpt,
        time_hint_sentences=time_hint_sentences,
        constraint_sentences=constraint_sentences,
        activity_hints=activity_hints,
        parser="reddit_json",
    )

    return UrlScrapeResult(
        ok=True,
        source_url=url,
        final_url=json_url,
        host=host,
        status_code=status_code,
        error=None,
        parser="reddit_json",
        page_title=page_title or None,
        meta_description=None,
        content_excerpt=content_excerpt or None,
        parsed_text_preview=(planning_text[:1200] or None),
        raw_text_preview=None,
        parsed_text_full=(main_text if include_full_text else None),
        raw_text_full=None,
        llm_condensed_preview=(llm_condensed_text[:1200] if llm_condensed_text else None),
        llm_condensed_full=(llm_condensed_text or None),
        llm_summary_model=llm_summary_model,
        llm_summary_error=llm_summary_error,
        llm_summary_trace=llm_summary_trace,
        activity_hints=activity_hints[:6],
        location_hints=location_hints[:6],
        vibe_tags=vibe_tags[:6],
        time_hints=time_hints,
        constraints=constraints,
        time_hint_sentences=time_hint_sentences[:3],
        constraint_sentences=constraint_sentences[:3],
        confidence=confidence,
    )


def _empty_failure_result(
    *,
    url: str,
    host: str,
    error: str,
    status_code: int | None = None,
    final_url: str | None = None,
    parser: str = "none",
) -> UrlScrapeResult:
    return UrlScrapeResult(
        ok=False,
        source_url=url,
        final_url=final_url or url,
        host=host,
        status_code=status_code,
        error=error,
        parser=parser,
        page_title=None,
        meta_description=None,
        content_excerpt=None,
        parsed_text_preview=None,
        raw_text_preview=None,
        parsed_text_full=None,
        raw_text_full=None,
        llm_condensed_preview=None,
        llm_condensed_full=None,
        llm_summary_model=None,
        llm_summary_error=None,
        llm_summary_trace=None,
        activity_hints=[],
        location_hints=[f"host:{host}"] if host else [],
        vibe_tags=[],
        time_hints=[],
        constraints=[],
        time_hint_sentences=[],
        constraint_sentences=[],
        confidence=0.2,
    )


def scrape_url_context(url: str, *, include_full_text: bool = False) -> UrlScrapeResult:
    """
    Fetch and parse a user-provided URL into planning-friendly signals.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        return _empty_failure_result(url=url, host=host, error="unsupported_scheme")

    if host in JS_CHALLENGE_DOMAINS:
        return _empty_failure_result(
            url=url,
            host=host,
            error=(
                f"js_challenge_blocked:{host} uses active bot protection (Cloudflare/DataDome) "
                "that requires a real browser. Use a search-based source instead."
            ),
        )

    if _is_reddit_url(host):
        timeout_seconds = _env_float("URL_SCRAPER_TIMEOUT_SECONDS", 8.0, minimum=3.0, maximum=20.0)
        max_excerpt_chars = _env_int("URL_SCRAPER_MAX_EXCERPT_CHARS", 2500, minimum=1250, maximum=5000)
        return _scrape_reddit_url(
            url,
            host=host,
            timeout_seconds=timeout_seconds,
            max_excerpt_chars=max_excerpt_chars,
            include_full_text=include_full_text,
        )

    timeout_seconds = _env_float(
        "URL_SCRAPER_TIMEOUT_SECONDS",
        8.0,
        minimum=3.0,
        maximum=20.0,
    )
    max_html_chars = _env_int(
        "URL_SCRAPER_MAX_HTML_CHARS",
        60000,
        minimum=40000,
        maximum=120000,
    )
    max_excerpt_chars = _env_int(
        "URL_SCRAPER_MAX_EXCERPT_CHARS",
        2500,
        minimum=1250,
        maximum=5000,
    )
    final_url = url
    status_code: int | None = None

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers=FETCH_HEADERS)
            final_url = str(response.url)
            status_code = response.status_code
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            if not (
                "text/html" in content_type
                or "text/plain" in content_type
                or content_type.startswith("application/xhtml")
            ):
                return _empty_failure_result(
                    url=url,
                    host=host,
                    status_code=status_code,
                    final_url=final_url,
                    parser="none",
                    error=f"unsupported_content_type:{content_type or 'unknown'}",
                )

            raw_response_text = response.text
            raw = raw_response_text if include_full_text else raw_response_text[:max_html_chars]
    except Exception as exc:  # noqa: BLE001
        return _empty_failure_result(url=url, host=host, error=str(exc))

    selector = Selector(text=raw) if Selector is not None else None
    raw_text_preview = _strip_html_text(raw, max_chars=1400)
    raw_text_full = _strip_html_text(raw, max_chars=None) if include_full_text else None
    page_title = _extract_title(raw, selector)
    meta_description = _extract_meta_description(raw, selector)
    parse_limit = len(raw) if include_full_text else max_html_chars
    main_text, parser = _extract_main_text(raw, selector, max_chars=parse_limit)

    llm_condensed_text, llm_summary_model, llm_summary_error, llm_summary_trace = _condense_with_llm(
        source_url=final_url or url,
        page_title=page_title,
        text=main_text,
    )
    planning_text = llm_condensed_text or main_text

    sentences = _split_sentences(planning_text)
    time_hint_sentences = _select_sentences(sentences, TIME_KEYWORDS, limit=4)
    constraint_sentences = _select_sentences(sentences, CONSTRAINT_KEYWORDS, limit=4)

    content_excerpt = _build_content_excerpt(
        text=planning_text,
        sentences=sentences,
        time_hint_sentences=time_hint_sentences,
        constraint_sentences=constraint_sentences,
        max_chars=max_excerpt_chars,
    )

    text_blob = " ".join([page_title, meta_description, content_excerpt]).strip()
    activity_hints = _extract_activity_hints(text_blob)
    vibe_tags = _extract_vibe_tags(text_blob)
    location_hints = _extract_location_hints(host, page_title, content_excerpt or planning_text)

    time_hints = [_truncate_hint(sentence) for sentence in time_hint_sentences[:3]]
    constraints = [_truncate_hint(sentence) for sentence in constraint_sentences[:3]]

    confidence = _score_confidence(
        page_title=page_title,
        meta_description=meta_description,
        content_excerpt=content_excerpt,
        time_hint_sentences=time_hint_sentences,
        constraint_sentences=constraint_sentences,
        activity_hints=activity_hints,
        parser=parser,
    )

    parsed_text_preview = planning_text or content_excerpt or meta_description or page_title
    parsed_text_full = main_text if include_full_text else None

    return UrlScrapeResult(
        ok=True,
        source_url=url,
        final_url=final_url,
        host=host,
        status_code=status_code,
        error=None,
        parser=parser,
        page_title=page_title or None,
        meta_description=meta_description or None,
        content_excerpt=content_excerpt or None,
        parsed_text_preview=(parsed_text_preview[:1200] or None),
        raw_text_preview=(raw_text_preview or None),
        parsed_text_full=(parsed_text_full or None),
        raw_text_full=(raw_text_full or None),
        llm_condensed_preview=(llm_condensed_text[:1200] if llm_condensed_text else None),
        llm_condensed_full=(llm_condensed_text or None),
        llm_summary_model=llm_summary_model,
        llm_summary_error=llm_summary_error,
        llm_summary_trace=llm_summary_trace,
        activity_hints=activity_hints[:6],
        location_hints=location_hints[:6],
        vibe_tags=vibe_tags[:6],
        time_hints=time_hints,
        constraints=constraints,
        time_hint_sentences=time_hint_sentences[:3],
        constraint_sentences=constraint_sentences[:3],
        confidence=confidence,
    )
