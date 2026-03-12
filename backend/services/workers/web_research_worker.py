"""
Web research worker: always runs and adds web-derived planning evidence.
"""

from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from models import EvidenceFacts, EvidenceItem, InputDirectives
from services.openrouter import build_web_queries

logger = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
DUCKDUCKGO_INSTANT_URL = "https://api.duckduckgo.com/"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_LITE_URL = "https://lite.duckduckgo.com/lite/"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_PROVIDER = "duckduckgo"
MAX_WEB_QUERIES = 5

GENERIC_STOPWORDS = {
    "plan",
    "trip",
    "travel",
    "with",
    "and",
    "for",
    "from",
    "the",
    "this",
    "that",
    "food",
    "days",
}


@dataclass(frozen=True)
class WebResearchResult:
    evidence: list[EvidenceItem]
    debug: dict[str, Any]


def _normalize_topic(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -_,.;:")
    return value


def _extract_focus_topics(idea_text: str, input_directives: InputDirectives) -> list[str]:
    topics: OrderedDict[str, str] = OrderedDict()

    def add_topic(candidate: str) -> None:
        normalized = _normalize_topic(candidate)
        if len(normalized) < 3:
            return
        lowered = normalized.lower()
        if lowered in GENERIC_STOPWORDS:
            return
        if lowered.startswith("plan "):
            normalized = _normalize_topic(normalized[5:])
            lowered = normalized.lower()
        if not normalized or lowered in topics:
            return
        topics[lowered] = normalized

    for must_include in input_directives.must_include:
        add_topic(must_include)

    proper_nouns = re.findall(
        r"\b(?:[A-Z][a-z]{2,}|[A-Z]{2,})(?:\s+(?:[A-Z][a-z]{2,}|[A-Z]{2,}))*\b",
        idea_text,
    )
    for noun in proper_nouns:
        add_topic(noun)

    if not topics:
        words = [
            token
            for token in re.findall(r"[A-Za-z]{3,}", idea_text)
            if token.lower() not in GENERIC_STOPWORDS
        ]
        if words:
            add_topic(" ".join(words[:3]))

    return list(topics.values())[:4]


def _build_queries_heuristic(
    idea_text: str,
    input_directives: InputDirectives,
    trip_location: str | None,
) -> list[str]:
    topics = _extract_focus_topics(idea_text, input_directives)
    queries: list[str] = []

    for topic in topics:
        queries.append(f"{topic} opening hours")
        queries.append(f"{topic} best time to visit")
        queries.append(f"{topic} official site")

    if not queries:
        short = " ".join(idea_text.split()[:6]).strip()
        if short:
            queries.extend(
                [
                    f"{short} opening hours",
                    f"{short} best time to visit",
                ]
            )

    hard_text = " ".join(input_directives.hard_constraints + input_directives.avoid).lower()
    if "sunset" in hard_text or "night" in hard_text:
        anchor = topics[0] if topics else "destination"
        queries.append(f"{anchor} sunset time")

    if trip_location:
        queries.append(f"{trip_location} official tourism site")
        queries.append(f"{trip_location} public transport options")

    ordered = list(OrderedDict.fromkeys(q.strip() for q in queries if q.strip()))
    return ordered[:MAX_WEB_QUERIES]


def _dedupe_queries(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = " ".join(value.split()).strip()
        if len(query) < 6:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped


def _contains_any_keyword(value: str, keywords: tuple[str, ...]) -> bool:
    lowered = value.lower()
    return any(keyword in lowered for keyword in keywords)


def _first_query_with_keywords(
    queries: list[str],
    keywords: tuple[str, ...],
) -> str | None:
    for query in queries:
        if _contains_any_keyword(query, keywords):
            return query
    return None


def _build_queries(
    *,
    idea_text: str,
    input_directives: InputDirectives,
    trip_location: str | None,
) -> tuple[list[str], dict[str, Any]]:
    heuristic_queries = _build_queries_heuristic(
        idea_text,
        input_directives,
        trip_location,
    )
    model_queries, model_debug = build_web_queries(
        idea_text=idea_text,
        trip_location=trip_location,
        hard_constraints=input_directives.hard_constraints,
        soft_preferences=input_directives.soft_preferences,
        must_include=input_directives.must_include,
        avoid=input_directives.avoid,
        max_queries=MAX_WEB_QUERIES,
    )

    query_source = "model"
    fallback_reason: str | None = None

    if not model_queries:
        query_source = "heuristic"
        fallback_reason = (
            "model returned no usable queries"
            if model_debug.get("error") is None
            else f"model query builder failed: {model_debug.get('error')}"
        )
        selected = heuristic_queries
    else:
        selected = _dedupe_queries([*model_queries, *heuristic_queries])[:MAX_WEB_QUERIES]

    needs_official = not any(
        _contains_any_keyword(query, ("official", "site", "tourism"))
        for query in selected
    )
    if needs_official:
        official_candidate = _first_query_with_keywords(
            heuristic_queries,
            ("official", "site", "tourism"),
        )
        if official_candidate:
            selected = _dedupe_queries([*selected, official_candidate])[:MAX_WEB_QUERIES]

    needs_hours = not any(
        _contains_any_keyword(query, ("opening hours", "hours"))
        for query in selected
    )
    if needs_hours:
        hours_candidate = _first_query_with_keywords(
            heuristic_queries,
            ("opening hours", "hours"),
        )
        if hours_candidate:
            selected = _dedupe_queries([*selected, hours_candidate])[:MAX_WEB_QUERIES]

    needs_booking = not any(
        _contains_any_keyword(query, ("book", "booking", "ticket", "reservation"))
        for query in selected
    )
    if needs_booking:
        booking_candidate = _first_query_with_keywords(
            heuristic_queries,
            ("book", "booking", "ticket", "reservation"),
        )
        if booking_candidate:
            selected = _dedupe_queries([*selected, booking_candidate])[:MAX_WEB_QUERIES]

    debug = {
        "query_source": query_source,
        "fallback_reason": fallback_reason,
        "heuristic_queries": heuristic_queries,
        "model_queries": model_queries,
        "queries_final": selected,
        "model_debug": model_debug,
    }
    return selected, debug


def _search_brave(query: str, api_key: str) -> list[dict[str, str]]:
    headers = {"X-Subscription-Token": api_key}
    params = {"q": query, "count": 4, "search_lang": "en"}

    with httpx.Client(timeout=6.0) as client:
        response = client.get(BRAVE_SEARCH_URL, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()

    entries = payload.get("web", {}).get("results", [])
    results: list[dict[str, str]] = []
    for item in entries:
        url = item.get("url")
        title = item.get("title") or ""
        desc = item.get("description") or ""
        if url:
            results.append({"url": url, "title": title, "snippet": desc})
    return results


def _search_tavily(query: str, api_key: str) -> list[dict[str, str]]:
    search_depth = os.getenv("TAVILY_SEARCH_DEPTH", "basic").strip().lower()
    if search_depth not in {"basic", "advanced"}:
        search_depth = "basic"

    topic = os.getenv("TAVILY_TOPIC", "general").strip().lower()
    if topic not in {"general", "news"}:
        topic = "general"

    max_results_raw = os.getenv("TAVILY_MAX_RESULTS", "6")
    try:
        max_results = int(max_results_raw)
    except ValueError:
        max_results = 6
    max_results = max(1, min(max_results, 10))

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "topic": topic,
        "include_answer": False,
        "include_raw_content": False,
        "max_results": max_results,
    }

    with httpx.Client(timeout=10.0) as client:
        response = client.post(TAVILY_SEARCH_URL, json=payload)
        response.raise_for_status()
        data = response.json()

    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        return []

    results: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        title = str(item.get("title") or "")
        snippet = str(item.get("content") or item.get("snippet") or "")
        results.append({"url": url, "title": title, "snippet": snippet})
    return results


def _clean_html_text(fragment: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _normalize_duckduckgo_result_url(raw_href: str) -> str:
    href = unescape(raw_href).strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://duckduckgo.com{href}"

    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        redirected = parse_qs(parsed.query).get("uddg")
        if redirected:
            return unquote(redirected[0])
    return href


def _parse_duckduckgo_html_results(html: str) -> list[dict[str, str]]:
    anchor_matches = re.findall(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_matches = re.findall(
        r'<(?:a|div)[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    results: list[dict[str, str]] = []
    for index, (raw_href, raw_title) in enumerate(anchor_matches):
        url = _normalize_duckduckgo_result_url(raw_href)
        if not url or not url.startswith("http"):
            continue

        title = _clean_html_text(raw_title)
        snippet = (
            _clean_html_text(snippet_matches[index])
            if index < len(snippet_matches)
            else ""
        )
        results.append({"url": url, "title": title, "snippet": snippet})
        if len(results) >= 6:
            break

    return results


def _search_duckduckgo_html(query: str) -> list[dict[str, str]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        )
    }
    params = {"q": query, "kl": "us-en"}

    with httpx.Client(timeout=8.0, follow_redirects=True) as client:
        response = client.get(DUCKDUCKGO_HTML_URL, params=params, headers=headers)
        response.raise_for_status()
        return _parse_duckduckgo_html_results(response.text)


def _search_duckduckgo_instant(query: str) -> list[dict[str, str]]:
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "no_redirect": "1",
    }

    with httpx.Client(timeout=6.0) as client:
        response = client.get(DUCKDUCKGO_INSTANT_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    results: list[dict[str, str]] = []
    if payload.get("AbstractURL"):
        results.append(
            {
                "url": payload["AbstractURL"],
                "title": payload.get("Heading", ""),
                "snippet": payload.get("AbstractText", ""),
            }
        )

    for item in payload.get("RelatedTopics", []):
        if isinstance(item, dict) and item.get("FirstURL"):
            results.append(
                {
                    "url": item.get("FirstURL", ""),
                    "title": item.get("Text", ""),
                    "snippet": item.get("Text", ""),
                }
            )
        if len(results) >= 4:
            break

    return results


def _parse_duckduckgo_lite_results(html: str) -> list[dict[str, str]]:
    link_matches = re.findall(
        r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_matches = re.findall(
        r'class="result-snippet"[^>]*>(.*?)</(?:td|span)>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    results: list[dict[str, str]] = []
    for i, (href, title_html) in enumerate(link_matches):
        url = href.strip()
        if not url.startswith("http"):
            continue
        title = _clean_html_text(title_html)
        snippet = _clean_html_text(snippet_matches[i]) if i < len(snippet_matches) else ""
        results.append({"url": url, "title": title, "snippet": snippet})
        if len(results) >= 6:
            break
    return results


def _search_duckduckgo_lite(query: str) -> list[dict[str, str]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    with httpx.Client(timeout=8.0, follow_redirects=True) as client:
        response = client.post(DUCKDUCKGO_LITE_URL, data={"q": query}, headers=headers)
        response.raise_for_status()
        return _parse_duckduckgo_lite_results(response.text)


def _search_duckduckgo(query: str) -> list[dict[str, str]]:
    try:
        lite_results = _search_duckduckgo_lite(query)
        if lite_results:
            return lite_results
    except Exception:  # noqa: BLE001
        pass
    html_results = _search_duckduckgo_html(query)
    if html_results:
        return html_results
    return _search_duckduckgo_instant(query)


def _infer_time_hints(text_blob: str) -> list[str]:
    lowered = text_blob.lower()
    hints: list[str] = []
    if "morning" in lowered:
        hints.append("morning recommended")
    if "sunset" in lowered:
        hints.append("plan around sunset")
    if "night" in lowered:
        hints.append("night-time considerations mentioned")
    if "weekend" in lowered:
        hints.append("weekend crowd considerations")
    return hints


def _infer_constraints(text_blob: str) -> list[str]:
    lowered = text_blob.lower()
    constraints: list[str] = []
    if "open" in lowered or "hours" in lowered:
        constraints.append("check opening hours before scheduling")
    if "close" in lowered or "closed" in lowered:
        constraints.append("avoid scheduling near closing windows")
    if "reservation" in lowered or "book" in lowered:
        constraints.append("reservation may be required")
    return constraints


def run_web_research_worker(
    *,
    idea_text: str,
    input_directives: InputDirectives,
    trip_location: str | None = None,
) -> WebResearchResult:
    """
    Always run web research and return evidence rows.

    Provider behavior:
    - tavily: use Tavily Search when `TAVILY_API_KEY` is present.
    - brave: use Brave when `BRAVE_SEARCH_API_KEY` is present.
    - otherwise: fallback to DuckDuckGo (HTML -> Instant).
    """
    provider = os.getenv("WEB_RESEARCH_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    queries, query_debug = _build_queries(
        idea_text=idea_text,
        input_directives=input_directives,
        trip_location=trip_location,
    )
    logger.info(
        "web_research_worker input: provider=%s trip_location=%s idea_preview=%s queries=%s",
        provider,
        trip_location,
        " ".join(idea_text.split())[:420],
        queries,
    )

    evidence: list[EvidenceItem] = []
    query_outcomes: list[dict[str, Any]] = []

    for index, query in enumerate(queries, start=1):
        results: list[dict[str, str]] = []
        error: str | None = None

        try:
            if provider == "tavily":
                if tavily_key:
                    results = _search_tavily(query, tavily_key)
                    provider_used = "tavily"
                else:
                    results = _search_duckduckgo(query)
                    provider_used = "duckduckgo_fallback_no_tavily_key"
            elif provider == "brave":
                if brave_key:
                    results = _search_brave(query, brave_key)
                    provider_used = "brave"
                else:
                    results = _search_duckduckgo(query)
                    provider_used = "duckduckgo_fallback_no_brave_key"
            else:
                results = _search_duckduckgo(query)
                provider_used = "duckduckgo"
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            try:
                results = _search_duckduckgo(query)
                provider_used = "duckduckgo_fallback_error"
            except Exception as ddg_exc:  # noqa: BLE001
                provider_used = provider if provider else DEFAULT_PROVIDER
                error = f"{error} | ddg_fallback_failed: {ddg_exc}"

        citations = [item["url"] for item in results if item.get("url")][:3]
        combined_text = " ".join(
            f"{item.get('title', '')} {item.get('snippet', '')}" for item in results
        ).strip()

        snippet_digest = ""
        results_preview: list[dict[str, str]] = []
        if results:
            snippet_parts: list[str] = []
            for item in results[:3]:
                title = (item.get("title") or "").strip()
                snippet = (item.get("snippet") or "").strip()
                if snippet:
                    snippet_parts.append(f"{title}: {snippet}" if title else snippet)
                elif title:
                    snippet_parts.append(title)
                results_preview.append({
                    "url": item.get("url", ""),
                    "title": title,
                    "snippet": snippet[:300],
                })
            snippet_digest = " | ".join(snippet_parts)[:360].strip()
            summary = (
                f"Web search '{query}': {snippet_digest}"
                if snippet_digest
                else f"Web research for '{query}' returned {len(results)} result(s) with no snippet text."
            )
            confidence = 0.65
        else:
            summary = f"Web research found no results for '{query}'."
            if error:
                summary = f"{summary} Error: {error}"
            confidence = 0.25
            if "duckduckgo" in provider_used:
                logger.warning(
                    "DuckDuckGo returned 0 results for query=%r — likely bot detection / CAPTCHA. "
                    "Set TAVILY_API_KEY or BRAVE_SEARCH_API_KEY in .env to use a real search API.",
                    query,
                )

        evidence.append(
            EvidenceItem(
                id=f"ev_worker_web_{index}",
                source_type="web",
                source_ref=f"query:{quote_plus(query)}",
                summary=summary,
                facts=EvidenceFacts(
                    time_hints=_infer_time_hints(combined_text),
                    constraints=_infer_constraints(combined_text),
                ),
                confidence=confidence,
                citations=citations,
            )
        )
        query_outcomes.append(
            {
                "query": query,
                "provider": provider_used,
                "result_count": len(results),
                "error": error,
                "citations": citations,
                "snippet_digest": snippet_digest,
                "results_preview": results_preview,
            }
        )

    debug = {
        **query_debug,
        "provider_requested": provider,
        "provider_default": DEFAULT_PROVIDER,
        "tavily_key_present": bool(tavily_key),
        "brave_key_present": bool(brave_key),
        "queries_executed": queries,
        "query_outcomes": query_outcomes,
    }
    return WebResearchResult(evidence=evidence, debug=debug)
