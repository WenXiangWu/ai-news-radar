from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from ..hashing import sha256_structured
from ..ids import canonicalize_url
from .base import (
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpConnector,
    RawDocument,
    _header,
)


class HTMLCollectionConnector(HttpConnector):
    """Discover article links from one or more declared HTML listing pages."""

    adapter_name = "html_collection"

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        listing_urls = self.config.get("listing_urls")
        if isinstance(listing_urls, list):
            self.listing_urls = [
                str(value).strip() for value in listing_urls if str(value).strip()
            ]
        else:
            listing_url = str(
                self.config.get("listing_url")
                or self.config.get("url")
                or self.config.get("locator")
                or ""
            ).strip()
            self.listing_urls = [listing_url] if listing_url else []
        raw_prefixes = self.config.get("link_prefixes")
        if isinstance(raw_prefixes, list):
            self.link_prefixes = tuple(
                str(value).strip() for value in raw_prefixes if str(value).strip()
            )
        else:
            prefix = str(self.config.get("link_prefix") or "").strip()
            self.link_prefixes = (prefix,) if prefix else ()
        raw_excludes = self.config.get("exclude_prefixes")
        if isinstance(raw_excludes, list):
            self.exclude_prefixes = tuple(
                str(value).strip() for value in raw_excludes if str(value).strip()
            )
        else:
            self.exclude_prefixes = ()
        try:
            self.max_items = max(0, int(self.config.get("max_items", 0)))
        except (TypeError, ValueError):
            self.max_items = 0
        if not self.listing_urls:
            self._configuration_error = "requires listing_url or listing_urls"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.listing_urls:
            raise ValueError(f"{self.adapter_name} requires listing_url or listing_urls")
        current = Cursor.coerce(cursor)
        cached = current.metadata.get("items")
        cached_by_listing = _cached_items_by_listing(cached)
        items: list[DiscoveredItem] = []
        listing_cursors: dict[str, dict[str, Any]] = {}
        for index, listing_url in enumerate(self.listing_urls):
            key = str(index)
            child_cursor = Cursor.coerce(
                current.metadata.get("listing_cursors", {}).get(key)
                if isinstance(current.metadata.get("listing_cursors"), Mapping)
                else None
            )
            response = self._request(
                listing_url,
                child_cursor,
                expected_content_types=(
                    "text/html",
                    "application/xhtml+xml",
                    "text/plain",
                ),
            )
            if response.status_code == 304:
                discovered = cached_by_listing.get(key, [])
                listing_cursors[key] = child_cursor.to_dict()
            else:
                discovered = self._parse_listing(listing_url, response.text)
                listing_cursors[key] = self._cursor(
                    response,
                    metadata={"listing_url": listing_url},
                    body=response.text,
                ).to_dict()
            items.extend(
                _item_from_payload(
                    self.source_id,
                    payload,
                    listing_index=index,
                )
                for payload in discovered
            )

        unique: dict[str, DiscoveredItem] = {}
        for item in items:
            unique.setdefault(item.url, item)
        ordered = list(unique.values())
        if self.max_items:
            ordered = ordered[: self.max_items]
        manifest = [
            {
                "listing_index": item.metadata.get("listing_index"),
                "url": item.url,
                "title": item.title,
            }
            for item in ordered
        ]
        page_cursor = Cursor(
            token=sha256_structured(manifest)[:32],
            metadata={
                "listing_cursors": listing_cursors,
                "items": [
                    _item_payload(item) for item in ordered
                ],
            },
        )
        return DiscoveryPage(
            items=ordered,
            cursor=page_cursor,
            metadata={"listing_urls": list(self.listing_urls)},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
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
                "text/markdown",
            ),
        )
        if response.status_code == 304:
            raise ValueError(
                f"{self.adapter_name} article was not modified without a body"
            )
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=response.text,
            content_type=response.content_type or "text/html",
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )

    def _parse_listing(self, listing_url: str, body: str) -> list[dict[str, Any]]:
        parser = _AnchorParser()
        parser.feed(body)
        parser.close()
        base_host = urlsplit(listing_url).netloc
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for href, title in parser.links:
            url = canonicalize_url(urljoin(listing_url, href))
            if not url or url in seen:
                continue
            parts = urlsplit(url)
            if parts.netloc != base_host:
                continue
            path = parts.path or "/"
            if self.link_prefixes and not any(
                path.startswith(prefix) for prefix in self.link_prefixes
            ):
                continue
            if any(path.startswith(prefix) for prefix in self.exclude_prefixes):
                continue
            listing_path = urlsplit(listing_url).path.rstrip("/")
            if path.rstrip("/") == listing_path:
                continue
            seen.add(url)
            found.append(
                {
                    "native_id": url,
                    "url": url,
                    "title": " ".join(title.split()) or path.rsplit("/", 1)[-1],
                }
            )
        return found


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = next(
            (str(value) for key, value in attrs if key.lower() == "href" and value),
            "",
        )
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        self.links.append((self._href, "".join(self._text)))
        self._href = ""
        self._text = []


def _item_payload(item: DiscoveredItem) -> dict[str, Any]:
    return {
        "native_id": item.native_id,
        "url": item.url,
        "title": item.title,
        "published_at": item.published_at,
        "content_type": item.content_type,
        "metadata": dict(item.metadata),
    }


def _item_from_payload(
    source_id: str,
    payload: Mapping[str, Any],
    *,
    listing_index: int,
) -> DiscoveredItem:
    metadata = dict(payload.get("metadata") or {})
    metadata["listing_index"] = listing_index
    return DiscoveredItem(
        source_id=source_id,
        native_id=str(payload.get("native_id") or payload.get("url") or ""),
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or payload.get("url") or ""),
        published_at=(
            str(payload.get("published_at"))
            if payload.get("published_at")
            else None
        ),
        content_type=str(payload.get("content_type") or "text/html"),
        metadata=metadata,
    )


def _cached_items_by_listing(value: Any) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        index = metadata.get("listing_index") if isinstance(metadata, Mapping) else None
        if index is None:
            continue
        result.setdefault(str(index), []).append(dict(item))
    return result


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
