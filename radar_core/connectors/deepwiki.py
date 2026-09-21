from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

from ..ids import canonicalize_url
from .base import (
    ConnectorError,
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpConnector,
    RawDocument,
    _header,
    _redact_url,
    content_type_for_path,
)
from .github_tree import GitHubTreeConnector


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
        self.github_repo = _github_repo(self.config)
        self.github_ref = str(
            self.config.get("ref") or self.config.get("branch") or "main"
        ).strip()
        if self.github_repo:
            self.incremental_class = "revision-native"
            self._github = GitHubTreeConnector(
                {
                    **dict(self.config),
                    "repo": self.github_repo,
                    "ref": self.github_ref,
                    "transport": self.transport,
                    "timeout_seconds": self.timeout,
                    "max_response_bytes": self.max_response_bytes,
                    "max_retries": self.max_retries,
                    "sleep": self.sleep,
                }
            )
        else:
            self.incremental_class = "unsupported"
            self._github = None
        if not self.wiki_url and not self.github_repo:
            self._configuration_error = "requires deepwiki_url or url or github"

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.wiki_url and not self.github_repo:
            raise ValueError(
                f"{self.adapter_name} requires deepwiki_url or url or github"
            )
        current = Cursor.coerce(cursor)

        if self._github is not None:
            return self._discover_via_github(current)

        return self._discover_via_html(current)

    def _discover_via_github(self, current: Cursor) -> DiscoveryPage:
        github_page = self._github.discover(current)
        if github_page.metadata.get("not_modified"):
            return DiscoveryPage(
                items=[],
                cursor=github_page.cursor,
                metadata={
                    "not_modified": True,
                    "authority": "github_tree",
                    "wiki_url": self.wiki_url,
                    "tree_url": github_page.metadata.get("tree_url"),
                },
            )
        items = [
            _remap_to_deepwiki(
                item,
                wiki_url=self.wiki_url,
                repo=self.github_repo,
                ref=self.github_ref,
            )
            for item in github_page.items
        ]
        return DiscoveryPage(
            items=items,
            cursor=github_page.cursor,
            metadata={
                "authority": "github_tree",
                "wiki_url": self.wiki_url,
                "tree_url": github_page.metadata.get("tree_url"),
                "truncated": bool(github_page.metadata.get("truncated", False)),
            },
        )

    def _discover_via_html(self, current: Cursor) -> DiscoveryPage:
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

        if self._github is not None:
            return self._fetch_github_backed(item)

        return self._fetch_html(item)

    def _fetch_github_backed(self, item: DiscoveredItem) -> RawDocument:
        deepwiki_url = item.url
        response = self._request_tolerant(
            deepwiki_url,
            item,
            expected_content_types=(
                "text/plain",
                "text/markdown",
                "text/html",
                "application/json",
            ),
        )
        if response.status_code == 404:
            blob_url = str(item.metadata.get("download_url") or "")
            if not blob_url:
                raise ConnectorError(
                    f"{self.adapter_name} page 404 and no GitHub blob fallback URL"
                )
            github_item = _restore_github_item(item, blob_url)
            return self._github.fetch(github_item)
        if response.status_code == 304:
            raise ValueError(
                f"{self.adapter_name} page was not modified without a body"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ConnectorError(
                f"{self.adapter_name} fetch failed with HTTP "
                f"{response.status_code} for {_redact_url(deepwiki_url)}"
            )
        self._validate_response(response, ("text/plain", "text/markdown", "text/html", "application/json"), deepwiki_url)
        body, content_type = _extract_json_page(response.text, response.content_type)
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=deepwiki_url,
            title=item.title,
            text=body,
            content_type=content_type or item.content_type or "text/markdown",
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )

    def _fetch_html(self, item: DiscoveredItem) -> RawDocument:
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

    def _request_tolerant(
        self,
        url: str,
        item: DiscoveredItem,
        *,
        expected_content_types: tuple[str, ...],
    ) -> Any:
        from .base import DEFAULT_USER_AGENT

        headers = {
            "Accept": ", ".join(expected_content_types) or "*/*",
            "User-Agent": str(self.config.get("user_agent") or DEFAULT_USER_AGENT),
        }
        etag = _optional_text(item.metadata.get("etag"))
        last_modified = _optional_text(item.metadata.get("last_modified"))
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return self._call_transport("GET", url, headers)


def _remap_to_deepwiki(
    item: DiscoveredItem,
    *,
    wiki_url: str,
    repo: str,
    ref: str,
) -> DiscoveredItem:
    path = str(item.metadata.get("path") or item.native_id)
    slug = _deepwiki_slug(path)
    page_url = canonicalize_url(f"{wiki_url.rstrip('/')}/{slug}")
    metadata = dict(item.metadata)
    metadata["deepwiki_url"] = page_url
    metadata["wiki_url"] = wiki_url
    return DiscoveredItem(
        source_id=item.source_id,
        native_id=path,
        url=page_url,
        title=item.title,
        published_at=item.published_at,
        content_type=item.content_type,
        remote_revision=item.remote_revision,
        remote_etag=item.remote_etag,
        remote_last_modified=item.remote_last_modified,
        metadata=metadata,
    )


def _restore_github_item(item: DiscoveredItem, blob_url: str) -> DiscoveredItem:
    return DiscoveredItem(
        source_id=item.source_id,
        native_id=item.native_id,
        url=canonicalize_url(blob_url),
        title=item.title,
        published_at=item.published_at,
        content_type=item.content_type,
        remote_revision=item.remote_revision,
        remote_etag=item.remote_etag,
        remote_last_modified=item.remote_last_modified,
        metadata=dict(item.metadata),
    )


def _deepwiki_slug(path: str) -> str:
    """Slug rule (spec §3.4): strip trailing `.md`/`.mdx`, keep nested path.

    `README.md` -> `README`; `docs/guide.md` -> `docs/guide`.
    """
    stem = path
    for suffix in (".md", ".mdx"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


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


def _github_repo(config: Mapping[str, Any]) -> str:
    for candidate in (
        config.get("github"),
        config.get("repo"),
    ):
        value = str(candidate or "").strip().strip("/")
        if value:
            return value
    for key in ("source", "task"):
        nested = config.get(key)
        if isinstance(nested, Mapping):
            for candidate in (
                nested.get("github"),
                nested.get("repo"),
            ):
                value = str(candidate or "").strip().strip("/")
                if value:
                    return value
            nested_source = nested.get("source")
            if isinstance(nested_source, Mapping):
                for candidate in (
                    nested_source.get("github"),
                    nested_source.get("repo"),
                ):
                    value = str(candidate or "").strip().strip("/")
                    if value:
                        return value
    return ""


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

    repo = _github_repo(config)
    if repo:
        return f"https://deepwiki.com/{repo}"
    return ""
