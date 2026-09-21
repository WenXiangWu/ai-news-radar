from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SourceItemRow:
    source_id: str
    item_id: str
    canonical_url: str
    remote_revision: str | None
    fetched_revision: str | None
    status: str


def select_items(
    items: list[dict[str, Any]],
    *,
    max_new_items: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (selected, deferred). Selected when fetched_revision != remote_revision."""
    changed = [
        item
        for item in items
        if str(item.get("status") or "") not in {"missing", "blocked"}
        and item.get("fetched_revision") != item.get("remote_revision")
    ]
    if max_new_items <= 0:
        return [], list(changed)
    selected = changed[:max_new_items]
    deferred = changed[max_new_items:]
    return selected, deferred
