"""
URL worker: classify and enrich user-supplied links into evidence.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
from urllib.parse import urlparse

from models import EvidenceDebug, EvidenceFacts, EvidenceItem
from services.workers.url_scraper import scrape_url_context

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        parsed = int(raw)
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _source_type_for_host(host: str) -> str:
    lowered = host.lower()
    if "instagram.com" in lowered:
        return "instagram"
    if "tiktok.com" in lowered:
        return "tiktok"
    return "web"


def _first_sentence(text: str | None, *, max_chars: int = 180) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return None
    first = cleaned.split(". ")[0].strip()
    if not first:
        return None
    if len(first) <= max_chars:
        return first
    return f"{first[: max_chars - 3].rstrip()}..."


def _build_evidence_for_link(
    *,
    index: int,
    link: str,
    include_full_text: bool,
) -> EvidenceItem:
    logger.info(
        "url_worker input: index=%s include_full_text=%s link=%s",
        index,
        include_full_text,
        link,
    )
    parsed = urlparse(link)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/") or "root"
    source_type = _source_type_for_host(host)

    if parsed.scheme not in {"http", "https"}:
        return EvidenceItem(
            id=f"ev_worker_url_{index}",
            source_type="web",
            source_ref=link,
            summary=(
                "URL worker skipped unsupported link scheme "
                f"(host={host or 'unknown'}, path={path})."
            ),
            facts=EvidenceFacts(
                vibe_tags=[f"host:{host}" if host else "host:unknown", f"path:{path}"]
            ),
            confidence=0.2,
            citations=[link],
            debug=EvidenceDebug(fetch_status="unsupported_scheme"),
        )

    if source_type in {"instagram", "tiktok"}:
        return EvidenceItem(
            id=f"ev_worker_url_{index}",
            source_type=source_type,
            source_ref=link,
            summary=(
                f"URL worker captured social link ({source_type}) "
                f"(host={host}, path={path}). Detailed scraping skipped for now."
            ),
            facts=EvidenceFacts(
                vibe_tags=[f"host:{host}", f"path:{path}", "social_link_uploaded"]
            ),
            confidence=0.45,
            citations=[link],
            debug=EvidenceDebug(fetch_status="skipped_social_requires_specialized_api"),
        )

    scrape = scrape_url_context(link, include_full_text=include_full_text)
    if not scrape.ok:
        return EvidenceItem(
            id=f"ev_worker_url_{index}",
            source_type="web",
            source_ref=link,
            summary=(
                f"URL scraping failed for host={host or 'unknown'} "
                f"(path={path}). Continuing with minimal link context."
            ),
            facts=EvidenceFacts(
                locations=scrape.location_hints[:4],
                vibe_tags=[
                    *scrape.vibe_tags[:4],
                    f"host:{host}" if host else "host:unknown",
                    f"path:{path}",
                ],
            ),
            confidence=scrape.confidence,
            citations=[scrape.final_url or link],
            debug=EvidenceDebug(
                fetch_status=(
                    f"error:{scrape.error}:{scrape.parser}"
                    if scrape.error
                    else f"error:{scrape.parser}"
                ),
                page_title=scrape.page_title,
                content_excerpt=scrape.content_excerpt,
                parsed_text_preview=scrape.parsed_text_preview,
                raw_text_preview=scrape.raw_text_preview,
                parsed_text_full=scrape.parsed_text_full,
                raw_text_full=scrape.raw_text_full,
                llm_condensed_preview=scrape.llm_condensed_preview,
                llm_condensed_full=scrape.llm_condensed_full,
                llm_summary_model=scrape.llm_summary_model,
                llm_summary_error=scrape.llm_summary_error,
                llm_summary_trace=scrape.llm_summary_trace,
                time_hint_sentences=scrape.time_hint_sentences[:3],
                constraint_sentences=scrape.constraint_sentences[:3],
            ),
        )

    summary_bits: list[str] = []
    if scrape.page_title:
        summary_bits.append(scrape.page_title)
    excerpt_lede = _first_sentence(scrape.content_excerpt)
    if excerpt_lede:
        summary_bits.append(excerpt_lede)
    elif scrape.meta_description:
        summary_bits.append(scrape.meta_description)
    summary = (
        f"URL scraping succeeded for host={host or scrape.host}. "
        + (" | ".join(summary_bits[:2]) if summary_bits else f"path={path}.")
    )

    vibe_tags = [*scrape.vibe_tags, f"host:{host}" if host else "host:unknown", f"path:{path}"]

    return EvidenceItem(
        id=f"ev_worker_url_{index}",
        source_type="web",
        source_ref=link,
        summary=summary,
        facts=EvidenceFacts(
            locations=scrape.location_hints[:5],
            activities=scrape.activity_hints[:6],
            time_hints=scrape.time_hints[:4],
            constraints=scrape.constraints[:4],
            vibe_tags=vibe_tags[:8],
        ),
        confidence=scrape.confidence,
        citations=[scrape.final_url or link],
        debug=EvidenceDebug(
            fetch_status=(
                f"ok:{scrape.status_code}:{scrape.parser}"
                if scrape.status_code
                else f"ok:{scrape.parser}"
            ),
            page_title=scrape.page_title,
            content_excerpt=scrape.content_excerpt,
            parsed_text_preview=scrape.parsed_text_preview,
            raw_text_preview=scrape.raw_text_preview,
            parsed_text_full=scrape.parsed_text_full,
            raw_text_full=scrape.raw_text_full,
            llm_condensed_preview=scrape.llm_condensed_preview,
            llm_condensed_full=scrape.llm_condensed_full,
            llm_summary_model=scrape.llm_summary_model,
            llm_summary_error=scrape.llm_summary_error,
            llm_summary_trace=scrape.llm_summary_trace,
            time_hint_sentences=scrape.time_hint_sentences[:3],
            constraint_sentences=scrape.constraint_sentences[:3],
        ),
    )


def run_url_context_worker(
    links: list[str],
    *,
    include_full_text: bool = False,
) -> list[EvidenceItem]:
    """
    Build URL evidence from uploaded links.

    - Social links (Instagram/TikTok) are captured in lightweight fallback mode.
    - Non-social web links are scraped through `url_scraper.py` for richer facts.
    """
    max_links = _env_int("URL_WORKER_MAX_LINKS", 8, minimum=1, maximum=40)
    link_parallelism = _env_int("URL_WORKER_LINK_PARALLELISM", 3, minimum=1, maximum=16)
    selected_links = links[:max_links]
    logger.info(
        "url_worker run: selected_links=%s max_links=%s link_parallelism=%s include_full_text=%s",
        len(selected_links),
        max_links,
        link_parallelism,
        include_full_text,
    )

    if len(selected_links) <= 1 or link_parallelism <= 1:
        return [
            _build_evidence_for_link(
                index=index,
                link=link,
                include_full_text=include_full_text,
            )
            for index, link in enumerate(selected_links, start=1)
        ]

    evidence_by_index: dict[int, EvidenceItem] = {}
    with ThreadPoolExecutor(max_workers=min(link_parallelism, len(selected_links))) as pool:
        future_by_index = {
            pool.submit(
                _build_evidence_for_link,
                index=index,
                link=link,
                include_full_text=include_full_text,
            ): index
            for index, link in enumerate(selected_links, start=1)
        }

        for future in as_completed(future_by_index):
            index = future_by_index[future]
            link = selected_links[index - 1]
            try:
                evidence_by_index[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                parsed = urlparse(link)
                host = parsed.netloc.lower()
                path = parsed.path.strip("/") or "root"
                evidence_by_index[index] = EvidenceItem(
                    id=f"ev_worker_url_{index}",
                    source_type="web",
                    source_ref=link,
                    summary=(
                        f"URL worker crashed while processing host={host or 'unknown'} "
                        f"(path={path})."
                    ),
                    facts=EvidenceFacts(
                        vibe_tags=[
                            f"host:{host}" if host else "host:unknown",
                            f"path:{path}",
                        ]
                    ),
                    confidence=0.2,
                    citations=[link],
                    debug=EvidenceDebug(fetch_status=f"error:worker_exception:{exc}"),
                )

    return [evidence_by_index[index] for index in range(1, len(selected_links) + 1)]
