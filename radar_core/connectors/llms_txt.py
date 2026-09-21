from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urljoin

from ..ids import canonicalize_url
from .base import (
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpConnector,
    RawDocument,
    _header,
    content_type_for_path,
)


_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)"
)


class LLMSTxtConnector(HttpConnector):
    adapter_name = "llms_txt"
    incremental_class: str | None = None

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.llms_url = str(
            self.config.get("llms_url")
            or self.config.get("url")
            or self.config.get("locator")
            or ""
        ).strip()
        if not self.llms_url:
            self._configuration_error = "requires llms_url or url"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.llms_url:
            raise ValueError(f"{self.adapter_name} requires llms_url or url")
        current = Cursor.coerce(cursor)
        response = self._request(
            self.llms_url,
            current,
            expected_content_types=(
                "text/plain",
                "text/markdown",
                "text/html",
            ),
        )
        if response.status_code == 304:
            if self.incremental_class is None:
                self.incremental_class = "revision-native"
            return DiscoveryPage(
                items=[],
                cursor=_preserve_cursor(current, response),
                metadata={"not_modified": True, "llms_url": self.llms_url},
            )

        body = response.text
        etag = _header(response.headers, "ETag")
        last_modified = _header(response.headers, "Last-Modified")
        remote_revision = etag or last_modified or None
        if remote_revision:
            self.incremental_class = "revision-native"
        else:
            self.incremental_class = "unsupported"

        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for title, raw_url in _MARKDOWN_LINK_RE.findall(body):
            url = canonicalize_url(urljoin(self.llms_url, raw_url))
            if not url or url in seen:
                continue
            seen.add(url)
            items.append(
                DiscoveredItem(
                    source_id=self.source_id,
                    native_id=url,
                    url=url,
                    title=title.strip() or url.rsplit("/", 1)[-1],
                    content_type=content_type_for_path(url),
                    remote_revision=remote_revision,
                    remote_etag=etag,
                    remote_last_modified=last_modified,
                    metadata={"llms_url": self.llms_url},
                )
            )

        if not items:
            items.append(
                DiscoveredItem(
                    source_id=self.source_id,
                    native_id="llms_txt",
                    url=self.llms_url,
                    title=self.source_id,
                    content_type="text/plain",
                    remote_revision=remote_revision,
                    remote_etag=etag,
                    remote_last_modified=last_modified,
                    metadata={"llms_url": self.llms_url, "inline_body": body},
                )
            )

        page_cursor = self._cursor(
            response,
            metadata={"llms_url": self.llms_url},
            body=body,
        )
        return DiscoveryPage(
            items=items,
            cursor=page_cursor,
            metadata={"llms_url": self.llms_url},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        inline_body = str(item.metadata.get("inline_body") or "")
        if inline_body and item.url == self.llms_url:
            return RawDocument(
                source_id=item.source_id,
                native_id=item.native_id,
                url=item.url,
                title=item.title,
                text=inline_body,
                content_type=item.content_type or "text/plain",
                published_at=item.published_at,
                metadata=dict(item.metadata),
            )

        response = self._request(
            item.url,
            Cursor(
                etag=_optional_text(item.metadata.get("etag")),
                last_modified=_optional_text(item.metadata.get("last_modified")),
            ),
            expected_content_types=(
                "text/plain",
                "text/markdown",
                "text/html",
                "application/json",
            ),
        )
        if response.status_code == 304:
            raise ValueError(f"{self.adapter_name} document was not modified without a body")
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=response.text,
            content_type=response.content_type or item.content_type or "text/plain",
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )


LLMSConnector = LLMSTxtConnector


def _preserve_cursor(current: Cursor, response: Any) -> Cursor:
    return Cursor(
        token=current.token,
        etag=_header(response.headers, "ETag") or current.etag,
        last_modified=_header(response.headers, "Last-Modified") or current.last_modified,
        metadata=dict(current.metadata),
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
