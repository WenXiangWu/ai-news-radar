"""TDD for spec §3.4: DeepWiki uses GitHub blob SHA as revision authority.

When `source.github` is set, the DeepWiki connector must:

- discover the item set from the GitHub git/trees API (blob SHA = revision),
- never use the DeepWiki root HTML link list as the item set,
- map each item to a DeepWiki page URL (`{deepwiki_url}/{slug}`),
- fall back to the GitHub blob@SHA on 404 during fetch,
- declare `incremental_class = "revision-native"`.

Without `source.github`, the connector declares `incremental_class = "unsupported"`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from radar_core.connectors.base import (
    Cursor,
    DiscoveredItem,
    HttpResponse,
)
from radar_core.connectors.deepwiki import DeepWikiConnector


def _github_tree_response(
    repo: str,
    ref: str,
    blobs: list[tuple[str, str]],
    *,
    etag: str = '"tree-v1"',
) -> HttpResponse:
    tree = [
        {
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": sha,
            "download_url": f"https://raw.githubusercontent.test/{repo}/{ref}/{path}",
        }
        for path, sha in blobs
    ]
    payload = {"sha": f"tree-{ref}-sha", "tree": tree, "truncated": False}
    return HttpResponse(
        status_code=200,
        headers={
            "Content-Type": "application/vnd.github+json",
            "ETag": etag,
        },
        body=json.dumps(payload).encode("utf-8"),
    )


class RouteTransport:
    """Deterministic transport that records every URL requested."""

    def __init__(self, routes: dict[str, HttpResponse]):
        self.routes = routes
        self.calls: list[str] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(url)
        if url not in self.routes:
            raise KeyError(f"unexpected request for {url}")
        return self.routes[url]


def _deepwiki_root_html(links: int) -> bytes:
    anchors = "".join(
        f'<a href="/acme/demo/page{i}">page{i}</a>' for i in range(links)
    )
    return f"<html><body>{anchors}</body></html>".encode("utf-8")


def test_deepwiki_discover_uses_github_sha_not_html_links():
    repo = "acme/demo"
    ref = "main"
    tree_url = (
        f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    )
    deepwiki_root = "https://deepwiki.com/acme/demo"
    transport = RouteTransport(
        {
            tree_url: _github_tree_response(
                repo,
                ref,
                [("README.md", "blob-readme-v1"), ("guide.md", "blob-guide-v1")],
            ),
            # A root page full of HTML links must NOT seed the item set.
            deepwiki_root: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html"},
                body=_deepwiki_root_html(50),
            ),
        }
    )
    connector = DeepWikiConnector(
        {
            "source_id": "source.deepwiki.acme",
            "github": repo,
            "ref": ref,
            "deepwiki_url": deepwiki_root,
            "transport": transport,
        }
    )

    assert connector.incremental_class == "revision-native"

    page = connector.discover(Cursor())

    assert {item.native_id for item in page.items} == {"README.md", "guide.md"}
    assert all(item.remote_revision for item in page.items)
    assert {item.remote_revision for item in page.items} == {
        "blob-readme-v1",
        "blob-guide-v1",
    }
    # URLs point to DeepWiki pages, not raw GitHub content.
    assert all(item.url.startswith(deepwiki_root + "/") for item in page.items)
    # The DeepWiki root HTML page must not be requested during discover.
    assert deepwiki_root not in transport.calls


def test_deepwiki_without_github_is_unsupported():
    connector = DeepWikiConnector(
        {"deepwiki_url": "https://deepwiki.com/acme/demo"}
    )
    assert connector.incremental_class == "unsupported"


def test_deepwiki_discover_304_from_github_does_not_fetch_deepwiki_root():
    repo = "acme/demo"
    ref = "main"
    tree_url = (
        f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    )
    deepwiki_root = "https://deepwiki.com/acme/demo"
    transport = RouteTransport(
        {
            tree_url: HttpResponse(
                status_code=304,
                headers={"ETag": '"tree-v1"'},
                body=b"",
            ),
            deepwiki_root: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html"},
                body=_deepwiki_root_html(10),
            ),
        }
    )
    connector = DeepWikiConnector(
        {
            "source_id": "source.deepwiki.acme",
            "github": repo,
            "ref": ref,
            "deepwiki_url": deepwiki_root,
            "transport": transport,
        }
    )

    page = connector.discover(Cursor(etag='"tree-v1"'))

    assert page.items == []
    assert page.metadata.get("not_modified") is True
    assert deepwiki_root not in transport.calls


def test_deepwiki_fetch_falls_back_to_github_blob_on_404():
    repo = "acme/demo"
    ref = "main"
    tree_url = (
        f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    )
    deepwiki_root = "https://deepwiki.com/acme/demo"
    deepwiki_page_url = f"{deepwiki_root}/README"
    blob_url = f"https://raw.githubusercontent.test/{repo}/{ref}/README.md"
    transport = RouteTransport(
        {
            tree_url: _github_tree_response(
                repo, ref, [("README.md", "blob-readme-v1")]
            ),
            deepwiki_page_url: HttpResponse(
                status_code=404,
                headers={"Content-Type": "text/html"},
                body=b"<html>not found</html>",
            ),
            blob_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown"},
                body=b"# README body from GitHub",
            ),
        }
    )
    connector = DeepWikiConnector(
        {
            "source_id": "source.deepwiki.acme",
            "github": repo,
            "ref": ref,
            "deepwiki_url": deepwiki_root,
            "transport": transport,
        }
    )

    page = connector.discover(Cursor())
    assert len(page.items) == 1
    item = page.items[0]

    document = connector.fetch(item)

    assert "README body from GitHub" in document.body
    assert document.content_type == "text/markdown"
    assert document.native_id == "README.md"
    # The fallback GitHub blob URL must have been requested.
    assert blob_url in transport.calls


def test_deepwiki_fetch_loads_deepwiki_page_when_available():
    repo = "acme/demo"
    ref = "main"
    tree_url = (
        f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    )
    deepwiki_root = "https://deepwiki.com/acme/demo"
    deepwiki_page_url = f"{deepwiki_root}/guide"
    transport = RouteTransport(
        {
            tree_url: _github_tree_response(
                repo, ref, [("guide.md", "blob-guide-v1")]
            ),
            deepwiki_page_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown"},
                body=b"# Guide from DeepWiki",
            ),
        }
    )
    connector = DeepWikiConnector(
        {
            "source_id": "source.deepwiki.acme",
            "github": repo,
            "ref": ref,
            "deepwiki_url": deepwiki_root,
            "transport": transport,
        }
    )

    page = connector.discover(Cursor())
    item = page.items[0]
    document = connector.fetch(item)

    assert "Guide from DeepWiki" in document.body
    assert item.remote_revision == "blob-guide-v1"
    assert document.metadata.get("sha") == "blob-guide-v1"
