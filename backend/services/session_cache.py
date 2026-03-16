"""
NovaSync — in-memory session cache for human-in-the-loop scaffold review.

Sessions are keyed by a UUID and expire after TTL_SECONDS. Eviction runs
lazily on every put/get call to avoid background threads.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from models import EvidenceItem

TTL_SECONDS: float = 15 * 60  # 15 minutes

_cache: dict[str, "PlanSession"] = {}


@dataclass
class PlanSession:
    session_id: str
    evidence: list[EvidenceItem]
    request_snapshot: dict[str, Any]          # trip_location, dates, timezone, directives
    scaffold_text: str                         # latest scaffold (updated on revision)
    idea_text: str                             # original user idea
    revision_count: int = 0
    created_at: float = field(default_factory=time.time)


def put_session(session: PlanSession) -> None:
    _evict_expired()
    _cache[session.session_id] = session


def get_session(session_id: str) -> PlanSession | None:
    _evict_expired()
    session = _cache.get(session_id)
    if session is None:
        return None
    if time.time() - session.created_at > TTL_SECONDS:
        del _cache[session_id]
        return None
    return session


def delete_session(session_id: str) -> None:
    _cache.pop(session_id, None)


def _evict_expired() -> None:
    now = time.time()
    expired = [sid for sid, s in _cache.items() if now - s.created_at > TTL_SECONDS]
    for sid in expired:
        del _cache[sid]
