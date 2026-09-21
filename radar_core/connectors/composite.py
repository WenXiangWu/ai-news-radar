from __future__ import annotations

from typing import Any, Mapping

from ..hashing import sha256_structured
from .base import (
    BaseConnector,
    ConnectorFactory,
    Connector,
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    RawDocument,
)


class CompositeConnector(BaseConnector):
    """Run a declarative list of child connectors under one source identity."""

    adapter_name = "composite"

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        raw_sources = self.config.get("sources")
        self.sources = [
            dict(source) for source in raw_sources
            if isinstance(source, Mapping)
        ] if isinstance(raw_sources, list) else []
        if not self.sources:
            self._configuration_error = "requires a non-empty sources list"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.sources:
            raise ValueError(f"{self.adapter_name} requires a non-empty sources list")
        current = Cursor.coerce(cursor)
        raw_child_cursors = current.metadata.get("child_cursors")
        child_cursors = (
            dict(raw_child_cursors)
            if isinstance(raw_child_cursors, Mapping)
            else {}
        )
        items: list[DiscoveredItem] = []
        next_child_cursors: dict[str, dict[str, Any]] = {}
        child_adapters: list[str] = []
        for index, source in enumerate(self.sources):
            adapter = str(source.get("adapter") or "").strip()
            if not adapter:
                raise ValueError(f"{self.adapter_name} source {index} requires adapter")
            child_adapters.append(adapter)
            child_config = dict(source)
            child_config["source_id"] = self.source_id
            child_config["transport"] = self.config.get("transport")
            _inherit_optional(child_config, self.config, "max_retries")
            _inherit_optional(child_config, self.config, "timeout_seconds")
            child = ConnectorFactory.create(adapter, child_config)
            page = child.discover(Cursor.coerce(child_cursors.get(str(index))))
            next_child_cursors[str(index)] = page.cursor.to_dict()
            for item in page.items:
                metadata = dict(item.metadata)
                metadata.update(
                    {
                        "composite_index": index,
                        "composite_adapter": adapter,
                        "composite_source": dict(source),
                    }
                )
                items.append(
                    DiscoveredItem(
                        source_id=self.source_id,
                        native_id=item.native_id,
                        url=item.url,
                        title=item.title,
                        published_at=item.published_at,
                        content_type=item.content_type,
                        remote_revision=item.remote_revision,
                        remote_etag=item.remote_etag,
                        remote_last_modified=item.remote_last_modified,
                        metadata=metadata,
                    )
                )
        return DiscoveryPage(
            items=items,
            cursor=Cursor(
                token=sha256_structured(
                    list(next_child_cursors.values())
                )[:32],
                metadata={
                    "child_cursors": next_child_cursors,
                    "child_adapters": child_adapters,
                },
            ),
            metadata={"child_count": len(self.sources)},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        raw_source = item.metadata.get("composite_source")
        if not isinstance(raw_source, Mapping):
            raise ValueError(f"{self.adapter_name} item has no child source")
        adapter = str(
            item.metadata.get("composite_adapter")
            or raw_source.get("adapter")
            or ""
        ).strip()
        if not adapter:
            raise ValueError(f"{self.adapter_name} item has no child adapter")
        child_config = dict(raw_source)
        child_config["source_id"] = self.source_id
        child_config["transport"] = self.config.get("transport")
        _inherit_optional(child_config, self.config, "max_retries")
        _inherit_optional(child_config, self.config, "timeout_seconds")
        child = ConnectorFactory.create(adapter, child_config)
        return child.fetch(item)


def _inherit_optional(
    target: dict[str, Any],
    parent: Mapping[str, Any],
    key: str,
) -> None:
    if key not in target and parent.get(key) is not None:
        target[key] = parent[key]
