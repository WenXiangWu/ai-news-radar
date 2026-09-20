from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .ids import revision_id_for
from .normalize import NormalizedDocument
from .storage import StateStore


@dataclass(frozen=True)
class Match:
    content_id: str
    kind: str
    reason: str


@dataclass(frozen=True)
class Revision:
    revision_id: str
    content_id: str
    source_hash: str
    normalizer_version: str
    is_new: bool


def _identity_payload(document: NormalizedDocument) -> dict[str, object]:
    return {
        "source_id": document.source_id,
        "native_id": document.native_id,
        "canonical_url": document.canonical_url,
        "title": document.title,
        "body": document.body,
        "published_at": document.published_at,
        "metadata": dict(document.metadata),
    }


def exact_match(document: NormalizedDocument, state: StateStore) -> Optional[Match]:
    existing = state.get_item(document.content_id)
    if existing is None:
        return None
    return Match(
        content_id=document.content_id,
        kind="exact",
        reason="stable_content_identity",
    )


def duplicate_candidates(
    document: NormalizedDocument,
    state: StateStore,
) -> list[Match]:
    candidates: list[Match] = []
    for revision in state.find_revisions_by_hash(document.source_hash):
        if revision["content_id"] == document.content_id:
            continue
        candidates.append(
            Match(
                content_id=str(revision["content_id"]),
                kind="possible_duplicate",
                reason="same_normalized_body",
            )
        )
    return candidates


def accept_revision(document: NormalizedDocument, state: StateStore) -> Revision:
    state.get_or_create_item(document.content_id, _identity_payload(document))
    revision_id = revision_id_for(document.source_hash, document.normalizer_version)
    existing = state.find_revision(
        content_id=document.content_id,
        source_hash=document.source_hash,
        normalizer_version=document.normalizer_version,
    )
    state.record_revision(
        {
            "revision_id": revision_id,
            "content_id": document.content_id,
            "source_hash": document.source_hash,
            "normalizer_version": document.normalizer_version,
            "status": "new",
            "title": document.title,
            "canonical_url": document.canonical_url,
        }
    )
    return Revision(
        revision_id=revision_id,
        content_id=document.content_id,
        source_hash=document.source_hash,
        normalizer_version=document.normalizer_version,
        is_new=existing is None,
    )
