"""
URL worker: classify and enrich user-supplied links into evidence.
"""

from __future__ import annotations

from urllib.parse import urlparse

from models import EvidenceFacts, EvidenceItem


def _source_type_for_host(host: str) -> str:
    lowered = host.lower()
    if "instagram.com" in lowered:
        return "instagram"
    if "tiktok.com" in lowered:
        return "tiktok"
    return "web"


def run_url_context_worker(links: list[str]) -> list[EvidenceItem]:
    """
    Produce lightweight URL evidence records.

    This worker does not scrape content yet. It normalizes domain/path context so
    downstream web-research and planning stages can reason over source intent.
    """
    evidence: list[EvidenceItem] = []

    for index, link in enumerate(links, start=1):
        parsed = urlparse(link)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/") or "root"
        source_type = _source_type_for_host(host)

        evidence.append(
            EvidenceItem(
                id=f"ev_worker_url_{index}",
                source_type=source_type,
                source_ref=link,
                summary=(
                    f"URL worker classified link as {source_type} "
                    f"(host={host}, path={path})."
                ),
                facts=EvidenceFacts(vibe_tags=[f"host:{host}", f"path:{path}"]),
                confidence=0.8,
                citations=[link],
            )
        )

    return evidence
