"""
Web grounding worker: fetch citation pages and extract structured scheduling facts.

Uses scrape_url_context for full LLM-based extraction, inheriting map-reduce
condensing, JS challenge blocking, Reddit JSON handling, and browser headers.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from models import EvidenceDebug, EvidenceFacts, EvidenceItem
from services.workers.url_scraper import scrape_url_context

logger = logging.getLogger(__name__)

MAX_GROUNDED_PAGES = 6


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
    Routes each citation URL through scrape_url_context for LLM-based extraction.
    """
    citations = _collect_citations(web_evidence)
    logger.info(
        "web_grounding_worker input: web_evidence=%s citations=%s",
        len(web_evidence),
        citations,
    )
    grounded: list[EvidenceItem] = []

    for index, citation_url in enumerate(citations, start=1):
        parsed = urlparse(citation_url)
        domain = parsed.netloc.lower() or "unknown"

        scrape = scrape_url_context(citation_url)

        if not scrape.ok:
            grounded.append(
                EvidenceItem(
                    id=f"ev_worker_grounded_web_{index}",
                    source_type="web",
                    source_ref=citation_url,
                    summary=(
                        f"Grounding fetch failed for {domain}: {scrape.error}. "
                        "Web fact extraction unavailable for this citation."
                    ),
                    facts=EvidenceFacts(
                        constraints=["grounding fetch failed; treat as unverified source"]
                    ),
                    confidence=0.2,
                    citations=[citation_url],
                    raw_artifact_ref=f"domain:{domain}",
                    debug=EvidenceDebug(
                        fetch_status=f"error:{scrape.error}",
                        page_title=None,
                        content_excerpt=None,
                        time_hint_sentences=[],
                        constraint_sentences=[],
                    ),
                )
            )
            continue

        summary_title = scrape.page_title or f"Page from {domain}"
        content = scrape.llm_condensed_full or scrape.content_excerpt or ""
        summary = (
            f"{summary_title}: {content[:300]}"
            if content
            else f"Grounded page from {summary_title}."
        )

        grounded.append(
            EvidenceItem(
                id=f"ev_worker_grounded_web_{index}",
                source_type="web",
                source_ref=citation_url,
                summary=summary,
                facts=EvidenceFacts(
                    locations=scrape.location_hints[:4],
                    activities=scrape.activity_hints[:4],
                    time_hints=scrape.time_hints[:4],
                    constraints=scrape.constraints[:4],
                    vibe_tags=[*scrape.vibe_tags[:3], f"domain:{domain}"],
                ),
                confidence=scrape.confidence,
                citations=[scrape.final_url or citation_url],
                raw_artifact_ref=f"domain:{domain}",
                debug=EvidenceDebug(
                    fetch_status=f"ok:{scrape.status_code}:{scrape.parser}",
                    page_title=summary_title,
                    content_excerpt=scrape.content_excerpt,
                    parsed_text_preview=scrape.parsed_text_preview,
                    llm_condensed_preview=scrape.llm_condensed_preview,
                    llm_summary_model=scrape.llm_summary_model,
                    llm_summary_error=scrape.llm_summary_error,
                    time_hint_sentences=scrape.time_hint_sentences[:3],
                    constraint_sentences=scrape.constraint_sentences[:3],
                ),
            )
        )

    return grounded
