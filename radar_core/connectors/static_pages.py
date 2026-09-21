from __future__ import annotations

from typing import Any, Mapping

from ..hashing import sha256_structured
from .base import (
    BaseConnector,
    ConnectorError,
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpConnector,
    RawDocument,
    _header,
)


class StaticPagesConnector(HttpConnector):
    """Fetch a bounded, explicitly registered list of documentation pages."""

    adapter_name = "static_pages"

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        raw_pages = self.config.get("pages")
        self.pages = [
            dict(page) for page in raw_pages
            if isinstance(page, Mapping)
        ] if isinstance(raw_pages, list) else []
        if not self.pages:
            self._configuration_error = "requires a non-empty pages list"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.pages:
            raise ValueError(f"{self.adapter_name} requires a non-empty pages list")
        items: list[DiscoveredItem] = []
        for index, page in enumerate(self.pages):
            url = str(page.get("url") or "").strip()
            if not url:
                raise ValueError(f"{self.adapter_name} page {index} requires url")
            native_id = str(page.get("id") or url).strip()
            items.append(
                DiscoveredItem(
                    source_id=self.source_id,
                    native_id=native_id,
                    url=url,
                    title=str(page.get("title") or native_id).strip(),
                    published_at=(
                        str(page.get("published_at"))
                        if page.get("published_at")
                        else None
                    ),
                    content_type=str(page.get("content_type") or "text/html"),
                    metadata={
                        "fetch_url": str(page.get("fetch_url") or url).strip(),
                        "page_index": index,
                    },
                )
            )
        token = sha256_structured(
            [
                {
                    "id": item.native_id,
                    "url": item.url,
                    "fetch_url": item.metadata.get("fetch_url"),
                }
                for item in items
            ]
        )[:32]
        return DiscoveryPage(
            items=items,
            cursor=Cursor(token=token, metadata={"pages": len(items)}),
            metadata={"pages": len(items)},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        fetch_url = str(item.metadata.get("fetch_url") or item.url).strip()
        urls = [fetch_url]
        if item.url and item.url != fetch_url:
            urls.append(item.url)
        last_error: ConnectorError | None = None
        response = None
        for url in urls:
            try:
                response = self._request(
                    url,
                    Cursor(
                        etag=_optional_text(item.metadata.get("etag")),
                        last_modified=_optional_text(item.metadata.get("last_modified")),
                    ),
                    expected_content_types=(
                        "text/html",
                        "application/xhtml+xml",
                        "text/plain",
                        "text/markdown",
                        "application/json",
                    ),
                )
                break
            except ConnectorError as exc:
                last_error = exc
        if response is None:
            if last_error is not None:
                raise last_error
            raise ConnectorError(f"{self.adapter_name} page has no fetch URL")
        if response.status_code == 304:
            raise ValueError(
                f"{self.adapter_name} page was not modified without a body"
            )
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=response.text,
            content_type=response.content_type or item.content_type,
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
