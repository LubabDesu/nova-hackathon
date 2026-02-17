"""
Web research worker: always runs and adds web-derived planning evidence.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from models import EvidenceFacts, EvidenceItem, InputDirectives

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
DUCKDUCKGO_INSTANT_URL = "https://api.duckduckgo.com/"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DEFAULT_PROVIDER = "duckduckgo"
MAX_WEB_QUERIES = 8

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


def _build_queries(idea_text: str, input_directives: InputDirectives) -> list[str]:
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

    ordered = list(OrderedDict.fromkeys(q.strip() for q in queries if q.strip()))
    return ordered[:MAX_WEB_QUERIES]


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


def _search_duckduckgo(query: str) -> list[dict[str, str]]:
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
) -> list[EvidenceItem]:
    """
    Always run web research and return evidence rows.

    The worker attempts Brave Search if `BRAVE_SEARCH_API_KEY` is set.
    Otherwise it uses DuckDuckGo HTML search parsing and falls back to
    DuckDuckGo Instant Answers if HTML parsing yields no results.
    """
    provider = os.getenv("WEB_RESEARCH_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    brave_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    queries = _build_queries(idea_text, input_directives)

    evidence: list[EvidenceItem] = []

    for index, query in enumerate(queries, start=1):
        results: list[dict[str, str]] = []
        error: str | None = None

        try:
            if provider == "brave" and brave_key:
                results = _search_brave(query, brave_key)
                provider_used = "brave"
            else:
                results = _search_duckduckgo(query)
                provider_used = "duckduckgo"
        except Exception as exc:  # noqa: BLE001
            provider_used = provider if provider else DEFAULT_PROVIDER
            error = str(exc)

        citations = [item["url"] for item in results if item.get("url")][:3]
        combined_text = " ".join(
            f"{item.get('title', '')} {item.get('snippet', '')}" for item in results
        ).strip()

        if results:
            summary = (
                f"Web research ({provider_used}) for '{query}' returned "
                f"{len(results)} result(s)."
            )
            confidence = 0.65
        else:
            summary = (
                f"Web research ({provider_used}) found no structured results for '{query}'."
            )
            if error:
                summary = f"{summary} Error: {error}"
            confidence = 0.25

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

    return evidence
