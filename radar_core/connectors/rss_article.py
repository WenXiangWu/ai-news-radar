from __future__ import annotations

from typing import Any, Mapping

import feedparser

from ..ids import canonicalize_url
from .base import (
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpConnector,
    RawDocument,
    _header,
)


class RSSArticleConnector(HttpConnector):
    adapter_name = "rss_article"
    incremental_class: str | None = None

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.feed_url = str(
            self.config.get("feed_url")
            or self.config.get("url")
            or self.config.get("locator")
            or ""
        ).strip()
        if not self.feed_url:
            self._configuration_error = "requires feed_url or url"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.feed_url:
            raise ValueError(f"{self.adapter_name} requires feed_url or url")
        current = Cursor.coerce(cursor)
        response = self._request(
            self.feed_url,
            current,
            expected_content_types=(
                "application/rss+xml",
                "application/atom+xml",
                "application/xml",
                "text/xml",
                "text/plain",
            ),
        )
        if response.status_code == 304:
            if self.incremental_class is None:
                self.incremental_class = "revision-native"
            return DiscoveryPage(
                items=[],
                cursor=_preserve_cursor(current, response),
                metadata={"not_modified": True, "feed_url": self.feed_url},
            )

        parsed = feedparser.parse(response.body)
        entries = list(parsed.get("entries") or [])
        if getattr(parsed, "bozo", False) and not entries:
            raise ValueError(f"{self.adapter_name} returned an invalid RSS document")

        feed_etag = _header(response.headers, "ETag")
        feed_last_modified = _header(response.headers, "Last-Modified")
        items: list[DiscoveredItem] = []
        missing_identity = False
        for entry in entries:
            link = _entry_link(entry)
            guid = _entry_text(entry, "id", "guid")
            native_id = guid or (canonicalize_url(link) if link else "") or link
            if not native_id:
                missing_identity = True
                continue
            url = canonicalize_url(link) if link else ""
            if not url:
                # Identity via guid alone is allowed; URL may fall back to feed.
                url = canonicalize_url(self.feed_url) or self.feed_url
            title = _entry_text(entry, "title") or native_id
            summary = _entry_content(entry)
            # Spec order: updated|published|etag|Last-Modified.
            updated = _entry_text(entry, "updated")
            published = _entry_text(entry, "published")
            remote_revision = (
                updated or published or feed_etag or feed_last_modified or None
            )
            items.append(
                DiscoveredItem(
                    source_id=self.source_id,
                    native_id=native_id,
                    url=url,
                    title=title,
                    published_at=published or updated or None,
                    content_type=(
                        "text/html"
                        if "<" in summary and ">" in summary
                        else "text/plain"
                    ),
                    remote_revision=remote_revision,
                    remote_etag=feed_etag,
                    remote_last_modified=feed_last_modified,
                    metadata={
                        "feed_url": self.feed_url,
                        "summary": summary,
                        "author": _entry_text(entry, "author"),
                        "raw_link": link,
                    },
                )
            )

        if missing_identity or not items or not any(
            item.has_remote_validator for item in items
        ):
            self.incremental_class = "unsupported"
        else:
            self.incremental_class = "revision-native"

        feed_token = _entry_text(parsed.get("feed") or {}, "updated", "published")
        if feed_etag or feed_last_modified:
            feed_token = None
        page_cursor = self._cursor(
            response,
            token=feed_token,
            metadata={"feed_url": self.feed_url},
            body=response.text,
        )
        return DiscoveryPage(
            items=items,
            cursor=page_cursor,
            metadata={"feed_url": self.feed_url},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        inline_body = str(
            item.metadata.get("content")
            or item.metadata.get("summary")
            or ""
        ).strip()
        if not item.url or item.url == self.feed_url:
            if not inline_body:
                raise ValueError(f"{self.adapter_name} item has no article URL or body")
            return RawDocument(
                source_id=item.source_id,
                native_id=item.native_id,
                url=item.url or self.feed_url,
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
                "text/html",
                "application/xhtml+xml",
                "text/plain",
                "application/xml",
                "text/xml",
            ),
        )
        if response.status_code == 304 and inline_body:
            body = inline_body
        elif response.status_code == 304:
            raise ValueError(f"{self.adapter_name} article was not modified without a body")
        else:
            body = response.text
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=body,
            content_type=response.content_type or item.content_type or "text/html",
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )


RSSConnector = RSSArticleConnector


def _entry_link(entry: Mapping[str, Any]) -> str:
    links = entry.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, Mapping) and link.get("href"):
                if str(link.get("rel") or "alternate") == "alternate":
                    return str(link["href"]).strip()
        for link in links:
            if isinstance(link, Mapping) and link.get("href"):
                return str(link["href"]).strip()
    return _entry_text(entry, "link")


def _entry_text(entry: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _entry_content(entry: Mapping[str, Any]) -> str:
    content = entry.get("content")
    if isinstance(content, list):
        for value in content:
            if isinstance(value, Mapping) and value.get("value"):
                return str(value["value"]).strip()
    return _entry_text(entry, "summary", "description")


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
