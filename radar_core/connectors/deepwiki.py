from __future__ import annotations

import json
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


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HTML_LINK_RE = re.compile(
    r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


class DeepWikiConnector(HttpConnector):
    adapter_name = "deepwiki"

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.wiki_url = _deepwiki_url(self.config)
        if not self.wiki_url:
            self._configuration_error = "requires deepwiki_url or url"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.wiki_url:
            raise ValueError(f"{self.adapter_name} requires deepwiki_url or url")
        current = Cursor.coerce(cursor)
        response = self._request(
            self.wiki_url,
            current,
            expected_content_types=(
                "text/plain",
                "text/markdown",
                "text/html",
                "application/json",
            ),
        )
        if response.status_code == 304:
            return DiscoveryPage(
                items=[],
                cursor=_preserve_cursor(current, response),
                metadata={"not_modified": True, "wiki_url": self.wiki_url},
            )

        items = _parse_discovered_items(
            response.text,
            source_id=self.source_id,
            root_url=self.wiki_url,
        )
        page_cursor = self._cursor(
            response,
            metadata={"wiki_url": self.wiki_url},
            body=response.text,
        )
        return DiscoveryPage(
            items=items,
            cursor=page_cursor,
            metadata={"wiki_url": self.wiki_url},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        inline_body = str(item.metadata.get("inline_body") or "")
        if inline_body:
            return RawDocument(
                source_id=item.source_id,
                native_id=item.native_id,
                url=item.url,
                title=item.title,
                text=inline_body,
                content_type=item.content_type or "text/markdown",
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
            raise ValueError(f"{self.adapter_name} page was not modified without a body")
        body, content_type = _extract_json_page(response.text, response.content_type)
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=body,
            content_type=content_type or item.content_type or "text/markdown",
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )


def _parse_discovered_items(
    text: str,
    *,
    source_id: str,
    root_url: str,
) -> list[DiscoveredItem]:
    records = _json_records(text)
    if records is not None:
        items = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            raw_url = str(
                record.get("url")
                or record.get("href")
                or record.get("link")
                or root_url
            ).strip()
            url = canonicalize_url(urljoin(root_url, raw_url))
            title = str(
                record.get("title")
                or record.get("name")
                or record.get("path")
                or url.rsplit("/", 1)[-1]
            ).strip()
            native_id = str(
                record.get("id")
                or record.get("slug")
                or record.get("path")
                or url
            ).strip()
            inline_body = _record_body(record)
            metadata = {"wiki_url": root_url}
            if inline_body:
                metadata["inline_body"] = inline_body
            items.append(
                DiscoveredItem(
                    source_id=source_id,
                    native_id=native_id,
                    url=url,
                    title=title,
                    content_type=content_type_for_path(url),
                    metadata=metadata,
                )
            )
        if items:
            return items

    links: list[tuple[str, str]] = []
    links.extend((title.strip(), url) for title, url in _MARKDOWN_LINK_RE.findall(text))
    links.extend(
        (_TAG_RE.sub("", title).strip(), url)
        for url, title in _HTML_LINK_RE.findall(text)
    )
    items = []
    seen: set[str] = set()
    for title, raw_url in links:
        url = canonicalize_url(urljoin(root_url, raw_url))
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(
            DiscoveredItem(
                source_id=source_id,
                native_id=url,
                url=url,
                title=title or url.rsplit("/", 1)[-1],
                content_type=content_type_for_path(url),
                metadata={"wiki_url": root_url},
            )
        )
    if items:
        return items
    return [
        DiscoveredItem(
            source_id=source_id,
            native_id="root",
            url=root_url,
            title=source_id,
            content_type="text/markdown",
            metadata={"wiki_url": root_url, "inline_body": text},
        )
    ]


def _json_records(text: str) -> list[Any] | None:
    if not text.lstrip().startswith(("{", "[")):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return None
    for key in ("pages", "documents", "items"):
        records = payload.get(key)
        if isinstance(records, list):
            return records
    if any(key in payload for key in ("content", "body", "text", "markdown")):
        return [payload]
    return None


def _record_body(record: Mapping[str, Any]) -> str:
    for key in ("content", "body", "text", "markdown"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _extract_json_page(text: str, content_type: str) -> tuple[str, str]:
    if content_type.endswith("json") or text.lstrip().startswith("{"):
        records = _json_records(text) or []
        if records and isinstance(records[0], Mapping):
            body = _record_body(records[0])
            if body:
                return body, "text/markdown"
    return text, content_type


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


def _deepwiki_url(config: Mapping[str, Any]) -> str:
    candidates: list[Any] = [
        config.get("deepwiki_url"),
        config.get("url"),
        config.get("locator"),
    ]
    for key in ("source", "task"):
        nested = config.get(key)
        if isinstance(nested, Mapping):
            candidates.extend(
                [
                    nested.get("deepwiki"),
                    nested.get("deepwiki_url"),
                    nested.get("url"),
                    nested.get("locator"),
                ]
            )
            nested_source = nested.get("source")
            if isinstance(nested_source, Mapping):
                candidates.extend(
                    [
                        nested_source.get("deepwiki"),
                        nested_source.get("deepwiki_url"),
                        nested_source.get("url"),
                        nested_source.get("locator"),
                    ]
                )

    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value

    github_candidates: list[Any] = [
        config.get("github"),
    ]
    for key in ("source", "task"):
        nested = config.get(key)
        if isinstance(nested, Mapping):
            github_candidates.append(nested.get("github"))
            nested_source = nested.get("source")
            if isinstance(nested_source, Mapping):
                github_candidates.append(nested_source.get("github"))
    for candidate in github_candidates:
        repository = str(candidate or "").strip().strip("/")
        if repository:
            return f"https://deepwiki.com/{repository}"
    return ""
