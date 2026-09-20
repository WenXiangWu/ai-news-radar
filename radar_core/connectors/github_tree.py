from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from ..ids import canonicalize_url
from .base import (
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpConnector,
    RawDocument,
    _header,
    content_type_for_path,
    parse_json_object,
)


class GitHubTreeConnector(HttpConnector):
    adapter_name = "github_tree"

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.repo = str(self.config.get("repo") or "").strip()
        self.ref = str(self.config.get("ref") or "main").strip()
        self.tree_url = str(
            self.config.get("tree_url")
            or self.config.get("api_url")
            or self._default_tree_url()
        ).strip()
        if not self.tree_url:
            self._configuration_error = "requires tree_url or repo"

    def _default_tree_url(self) -> str:
        if not self.repo:
            return ""
        return (
            "https://api.github.com/repos/"
            f"{quote(self.repo, safe='/')}/git/trees/{quote(self.ref, safe='')}"
            "?recursive=1"
        )

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        if not self.tree_url:
            raise ValueError(f"{self.adapter_name} requires tree_url or repo")
        current = Cursor.coerce(cursor)
        headers = {
            "Accept": "application/vnd.github+json",
        }
        token = str(self.config.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self._request(
            self.tree_url,
            current,
            expected_content_types=(
                "application/json",
                "application/vnd.github+json",
                "text/plain",
            ),
            extra_headers=headers,
        )
        if response.status_code == 304:
            return DiscoveryPage(
                items=[],
                cursor=_preserve_cursor(current, response),
                metadata={"not_modified": True, "tree_url": self.tree_url},
            )

        payload = parse_json_object(response.text, self.adapter_name)
        tree = payload.get("tree")
        if not isinstance(tree, list):
            raise ValueError(f"{self.adapter_name} response has no tree list")

        extensions = self.config.get(
            "extensions",
            (".md", ".mdx", ".txt", ".rst"),
        )
        allowed_extensions = {
            str(extension).lower()
            for extension in extensions
        }
        path_prefix = str(self.config.get("path_prefix") or "").strip().strip("/")
        items: list[DiscoveredItem] = []
        for entry in tree:
            if not isinstance(entry, Mapping) or entry.get("type") != "blob":
                continue
            path = str(entry.get("path") or "").strip()
            if not path:
                continue
            if path_prefix and not (
                path == path_prefix or path.startswith(path_prefix + "/")
            ):
                continue
            if not self.config.get("include_all", False):
                if Path(path).suffix.lower() not in allowed_extensions:
                    continue
            item_url = self._entry_url(entry, path)
            native_id = path
            blob_sha = str(entry.get("sha") or "")
            items.append(
                DiscoveredItem(
                    source_id=self.source_id,
                    native_id=native_id,
                    url=item_url,
                    title=Path(path).name or path,
                    content_type=content_type_for_path(path),
                    metadata={
                        "repo": self.repo,
                        "ref": self.ref,
                        "path": path,
                        "blob_url": str(entry.get("url") or ""),
                        "download_url": str(entry.get("download_url") or ""),
                        "sha": blob_sha,
                    },
                )
            )

        page_cursor = self._cursor(
            response,
            token=str(payload.get("sha") or "") or None,
            metadata={
                "tree_url": self.tree_url,
                "repo": self.repo,
                "ref": self.ref,
            },
            body=response.text,
        )
        return DiscoveryPage(
            items=items,
            cursor=page_cursor,
            metadata={
                "tree_url": self.tree_url,
                "truncated": bool(payload.get("truncated", False)),
            },
        )

    def _entry_url(self, entry: Mapping[str, Any], path: str) -> str:
        direct = str(entry.get("download_url") or "").strip()
        if direct:
            return canonicalize_url(direct)
        if self.repo:
            return (
                "https://raw.githubusercontent.com/"
                f"{self.repo}/{quote(self.ref, safe='')}/{quote(path, safe='/')}"
            )
        return str(entry.get("url") or path)

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        headers = {}
        token = str(self.config.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self._request(
            item.url,
            Cursor(
                etag=_optional_text(item.metadata.get("etag")),
                last_modified=_optional_text(item.metadata.get("last_modified")),
            ),
            expected_content_types=(
                "text/plain",
                "text/markdown",
                "application/json",
                "application/vnd.github+json",
                "application/octet-stream",
            ),
            extra_headers=headers,
        )
        if response.status_code == 304:
            raise ValueError(f"{self.adapter_name} blob was not modified without a body")

        body = response.text
        content_type = response.content_type or item.content_type or "text/plain"
        if content_type.endswith("json") or body.lstrip().startswith("{"):
            payload = parse_json_object(body, self.adapter_name)
            encoded = payload.get("content")
            if encoded:
                try:
                    decoded = base64.b64decode(
                        "".join(str(encoded).split()),
                        validate=False,
                    )
                    body = decoded.decode("utf-8", errors="replace")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError(f"{self.adapter_name} blob content is invalid") from exc
                if len(decoded) > self.max_response_bytes:
                    raise ValueError(
                        f"{self.adapter_name} decoded blob exceeds "
                        f"{self.max_response_bytes} bytes"
                    )
                content_type = item.content_type or content_type_for_path(
                    str(item.metadata.get("path") or item.title)
                )

        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=body,
            content_type=content_type,
            published_at=item.published_at,
            etag=_header(response.headers, "ETag"),
            last_modified=_header(response.headers, "Last-Modified"),
            metadata=dict(item.metadata),
        )


GithubTreeConnector = GitHubTreeConnector


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
