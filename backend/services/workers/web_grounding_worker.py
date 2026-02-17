"""
Web grounding worker: fetch citation pages and extract structured scheduling facts.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from models import EvidenceDebug, EvidenceFacts, EvidenceItem

MAX_GROUNDED_PAGES = 6
MAX_PAGE_CHARS = 18000
MAX_DEBUG_EXCERPT_CHARS = 350

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
}


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _short_excerpt(value: str, max_len: int = MAX_DEBUG_EXCERPT_CHARS) -> str:
    compact = _normalize_space(value)
    if len(compact) <= max_len:
        return compact
    return f"{compact[:max_len - 1]}…"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<title>(.*?)</title>", r" \1 ", html)
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    return _normalize_space(html)


def _extract_title(html: str) -> str:
    match = re.search(r"(?is)<title>(.*?)</title>", html)
    if not match:
        return ""
    return _normalize_space(match.group(1))


def _fetch_page_content(url: str) -> tuple[str, str]:
    with httpx.Client(timeout=8.0, follow_redirects=True) as client:
        response = client.get(url, headers=FETCH_HEADERS)
        response.raise_for_status()
        html = response.text[:MAX_PAGE_CHARS]
    return _extract_title(html), _html_to_text(html)


def _split_sentences(text: str) -> list[str]:
    raw_sentences = re.split(r"(?<=[.!?])\s+|\s{2,}", text)
    sentences: list[str] = []
    for raw in raw_sentences:
        cleaned = _normalize_space(raw)
        if 20 <= len(cleaned) <= 260:
            sentences.append(cleaned)
    return sentences


def _select_sentences(sentences: list[str], keywords: tuple[str, ...], limit: int = 3) -> list[str]:
    selected: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            selected.append(sentence)
        if len(selected) >= limit:
            break
    return selected


def _extract_time_hints(sentences: list[str]) -> list[str]:
    hints = _select_sentences(
        sentences,
        (
            "best time",
            "morning",
            "afternoon",
            "evening",
            "sunset",
            "night",
            "weekday",
            "weekend",
        ),
    )
    return hints


def _extract_constraints(sentences: list[str]) -> list[str]:
    constraints = _select_sentences(
        sentences,
        (
            "open",
            "opening hours",
            "closed",
            "hours",
            "reservation",
            "book",
            "ticket",
            "last entry",
        ),
    )
    return constraints


def _collect_citations(web_evidence: list[EvidenceItem]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in web_evidence:
        for citation in item.citations:
            url = citation.strip()
            if not url.startswith("http"):
                continue
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
            if len(deduped) >= MAX_GROUNDED_PAGES:
                return deduped
    return deduped


def run_web_grounding_worker(web_evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    """
    Ground web-search evidence by reading citation pages and extracting facts.
    """
    citations = _collect_citations(web_evidence)
    grounded: list[EvidenceItem] = []

    for index, citation_url in enumerate(citations, start=1):
        parsed = urlparse(citation_url)
        domain = parsed.netloc.lower() or "unknown"

        try:
            title, text = _fetch_page_content(citation_url)
            sentences = _split_sentences(text)
            time_hints = _extract_time_hints(sentences)
            constraints = _extract_constraints(sentences)

            summary_title = title if title else f"Page from {domain}"
            summary = f"Grounded facts extracted from {summary_title}."
            confidence = 0.85 if (time_hints or constraints) else 0.5

            grounded.append(
                EvidenceItem(
                    id=f"ev_worker_grounded_web_{index}",
                    source_type="web",
                    source_ref=citation_url,
                    summary=summary,
                    facts=EvidenceFacts(
                        time_hints=time_hints,
                        constraints=constraints,
                        vibe_tags=[f"domain:{domain}"],
                    ),
                    confidence=confidence,
                    citations=[citation_url],
                    raw_artifact_ref=f"domain:{domain}",
                    debug=EvidenceDebug(
                        fetch_status="ok",
                        page_title=summary_title,
                        content_excerpt=_short_excerpt(text),
                        time_hint_sentences=time_hints[:3],
                        constraint_sentences=constraints[:3],
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            grounded.append(
                EvidenceItem(
                    id=f"ev_worker_grounded_web_{index}",
                    source_type="web",
                    source_ref=citation_url,
                    summary=(
                        f"Grounding fetch failed for {domain}: {exc}. "
                        "Web fact extraction unavailable for this citation."
                    ),
                    facts=EvidenceFacts(
                        constraints=["grounding fetch failed; treat as unverified source"]
                    ),
                    confidence=0.2,
                    citations=[citation_url],
                    raw_artifact_ref=f"domain:{domain}",
                    debug=EvidenceDebug(
                        fetch_status=f"error:{exc.__class__.__name__}",
                        page_title=None,
                        content_excerpt=None,
                        time_hint_sentences=[],
                        constraint_sentences=[],
                    ),
                )
            )

    return grounded
