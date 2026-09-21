"""Connectors that still dropped remote_revision would become unsupported_incremental.

html_collection / static_pages must emit a stable identity. composite must copy
child revision fields instead of remapping a bare DiscoveredItem. github_tree
must resolve the repo default branch instead of assuming `main`.
"""

from __future__ import annotations

import json

from radar_core.connectors.base import ConnectorFactory, Cursor, HttpResponse
from radar_core.connectors.deepwiki import DeepWikiConnector
from radar_core.pipeline import _connector_config
from radar_core.registry import TaskSpec

from tests.test_connectors import FixtureTransport, SequenceTransport, fixture_bytes


def test_html_collection_emits_canonical_url_as_remote_revision():
    listing_url = "https://blog.example.test/engineering"
    article_url = "https://blog.example.test/engineering/agents"
    transport = FixtureTransport(
        {
            listing_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=fixture_bytes("html-collection-index.html"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "html_collection",
        {
            "source_id": "source.html.ledger",
            "listing_url": listing_url,
            "link_prefixes": ["/engineering/"],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "revision-native"
    assert page.items[0].url == article_url
    assert page.items[0].remote_revision == article_url
    assert page.items[0].has_remote_validator


def test_html_collection_304_reconstructs_remote_revision_from_cursor():
    listing_url = "https://blog.example.test/engineering"
    article_url = "https://blog.example.test/engineering/agents"
    transport = SequenceTransport(
        [
            HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "ETag": '"listing-v1"',
                },
                body=fixture_bytes("html-collection-index.html"),
            ),
            HttpResponse(
                status_code=304,
                headers={"ETag": '"listing-v1"'},
                body=b"",
            ),
        ]
    )
    connector = ConnectorFactory.create(
        "html_collection",
        {
            "source_id": "source.html.ledger",
            "listing_url": listing_url,
            "link_prefixes": ["/engineering/"],
            "transport": transport,
        },
    )

    first = connector.discover(Cursor())
    second = connector.discover(first.cursor)

    assert second.items[0].remote_revision == article_url
    assert second.items[0].has_remote_validator


def test_static_pages_uses_head_etag_as_remote_revision():
    public_url = "https://docs.example.test/guides/agents"
    transport = FixtureTransport(
        {
            public_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "ETag": '"page-v3"',
                    "Last-Modified": "Tue, 15 Sep 2026 11:00:00 GMT",
                },
                body=b"<html>unused during discover</html>",
            )
        }
    )
    connector = ConnectorFactory.create(
        "static_pages",
        {
            "source_id": "source.static.ledger",
            "pages": [
                {
                    "id": "agents",
                    "title": "Agents guide",
                    "url": public_url,
                }
            ],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "revision-native"
    assert page.items[0].remote_etag == '"page-v3"'
    assert page.items[0].remote_last_modified == "Tue, 15 Sep 2026 11:00:00 GMT"
    assert page.items[0].remote_revision == '"page-v3"'
    assert transport.calls[0]["method"] == "HEAD"
    assert transport.calls[0]["url"] == public_url


def test_static_pages_falls_back_to_url_when_head_has_no_validators():
    public_url = "https://docs.example.test/guides/agents"
    transport = SequenceTransport(
        [
            HttpResponse(
                status_code=405,
                headers={"Content-Type": "text/plain"},
                body=b"HEAD not allowed",
            )
        ]
    )
    connector = ConnectorFactory.create(
        "static_pages",
        {
            "source_id": "source.static.ledger",
            "pages": [
                {
                    "id": "agents",
                    "title": "Agents guide",
                    "url": public_url,
                }
            ],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert page.items[0].remote_revision == public_url
    assert page.items[0].has_remote_validator


def test_composite_copies_child_remote_revision_fields():
    feed_url = "https://composite.example.test/feed.xml"
    article_url = "https://composite.example.test/article"
    llms_url = "https://composite.example.test/llms.txt"
    transport = FixtureTransport(
        {
            feed_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/rss+xml"},
                body=fixture_bytes("rss.xml").replace(
                    b"https://news.example.test/articles/one",
                    article_url.encode("utf-8"),
                ),
            ),
            llms_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "text/plain",
                    "ETag": '"llms-v1"',
                },
                body=fixture_bytes("llms.txt"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "composite",
        {
            "source_id": "source.composite.ledger",
            "sources": [
                {"adapter": "rss_article", "feed_url": feed_url},
                {"adapter": "llms_txt", "url": llms_url},
            ],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert all(item.has_remote_validator for item in page.items)
    rss_items = [
        item for item in page.items if item.metadata["composite_adapter"] == "rss_article"
    ]
    llms_items = [
        item for item in page.items if item.metadata["composite_adapter"] == "llms_txt"
    ]
    assert all(item.remote_revision for item in rss_items)
    assert all(item.remote_revision == '"llms-v1"' for item in llms_items)
    assert all(item.remote_etag == '"llms-v1"' for item in llms_items)


def test_github_tree_resolves_repo_default_branch_when_ref_omitted():
    repo = "deepseek-ai/deepseek-harness"
    repo_url = f"https://api.github.com/repos/{repo}"
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/master?recursive=1"
    transport = FixtureTransport(
        {
            repo_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/vnd.github+json"},
                body=json.dumps({"default_branch": "master"}).encode("utf-8"),
            ),
            tree_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=fixture_bytes("github-tree.json"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "github_tree",
        {
            "source_id": "source.github.default-branch",
            "repo": repo,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert [call["url"] for call in transport.calls] == [repo_url, tree_url]
    assert connector.ref == "master"
    assert page.items
    assert all(item.remote_revision for item in page.items)


def test_deepwiki_resolves_github_default_branch_instead_of_assuming_main():
    repo = "deepseek-ai/deepseek-harness"
    repo_url = f"https://api.github.com/repos/{repo}"
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/master?recursive=1"
    deepwiki_root = "https://deepwiki.com/deepseek-ai/deepseek-harness"
    transport = FixtureTransport(
        {
            repo_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/vnd.github+json"},
                body=json.dumps({"default_branch": "master"}).encode("utf-8"),
            ),
            tree_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/vnd.github+json"},
                body=json.dumps(
                    {
                        "sha": "tree-master-sha",
                        "truncated": False,
                        "tree": [
                            {
                                "path": "README.md",
                                "mode": "100644",
                                "type": "blob",
                                "sha": "blob-readme-v1",
                            }
                        ],
                    }
                ).encode("utf-8"),
            ),
        }
    )
    connector = DeepWikiConnector(
        {
            "source_id": "source.deepwiki.harness",
            "github": repo,
            "deepwiki_url": deepwiki_root,
            "transport": transport,
        }
    )

    page = connector.discover(Cursor())

    assert [call["url"] for call in transport.calls] == [repo_url, tree_url]
    assert page.items[0].remote_revision == "blob-readme-v1"
    assert "git/trees/main" not in "".join(call["url"] for call in transport.calls)


def test_deepwiki_task_passes_module_ref_into_connector_config():
    task = TaskSpec.from_payload(
        {
            "id": "task.framework.deepseek-harness.wiki",
            "kind": "wiki_sync",
            "adapter": "deepwiki",
            "output": {
                "path": "frontend/path/frameworks/02-agent-runtime/deepseek-harness/wiki"
            },
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
            },
        },
        module={
            "id": "framework.deepseek-harness",
            "kind": "framework",
            "enabled": True,
            "display": {"name": "DeepSeek Harness"},
            "source": {
                "github": "deepseek-ai/deepseek-harness",
                "deepwiki": "https://deepwiki.com/deepseek-ai/deepseek-harness",
                "ref": "master",
            },
        },
    )

    config = _connector_config(task.to_source_spec(), "deepwiki")

    assert config["ref"] == "master"
    assert config["github"] == "deepseek-ai/deepseek-harness"
