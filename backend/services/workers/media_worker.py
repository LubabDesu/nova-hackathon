"""
Media worker: convert uploaded media metadata into evidence placeholders.
"""

from __future__ import annotations

from models import EvidenceFacts, EvidenceItem
from services.openrouter import MediaInput


def run_media_context_worker(media_inputs: list[MediaInput]) -> list[EvidenceItem]:
    """
    Create first-pass evidence from uploaded media files.

    This worker intentionally avoids deep vision/video parsing for now; it marks
    artifacts for downstream multimodal reasoning while preserving file context.
    """
    evidence: list[EvidenceItem] = []

    for index, media in enumerate(media_inputs, start=1):
        source_type = "upload_video" if media.mime_type.startswith("video/") else "upload_image"
        evidence.append(
            EvidenceItem(
                id=f"ev_worker_media_{index}",
                source_type=source_type,
                source_ref=media.filename,
                summary=(
                    f"Media worker registered {source_type.replace('_', ' ')} "
                    f"({media.mime_type}) for multimodal analysis."
                ),
                facts=EvidenceFacts(vibe_tags=[f"mime_type:{media.mime_type}"]),
                confidence=0.78,
            )
        )

    return evidence
