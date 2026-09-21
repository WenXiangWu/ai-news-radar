from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from radar_core.connectors.base import (
    ConnectorError,
    ConnectorFactory,
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    HttpResponse,
    RawDocument,
    UnsupportedConnectorError,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "source-documents"
ADAPTER_MATRIX = json.loads(
    (FIXTURE_ROOT / "adapter-matrix.json").read_text(encoding="utf-8")
)
REGISTERED_ADAPTERS = tuple(
    entry["adapter"] for entry in ADAPTER_MATRIX["adapters"]
)
NON_FETCHING_ADAPTERS = tuple(
    entry["adapter"]
    for entry in ADAPTER_MATRIX["adapters"]
    if entry["mode"] == "unsupported"
)


def fixture_text(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURE_ROOT / name).read_bytes()


class FixtureTransport:
    def __init__(self, routes: dict[str, HttpResponse | str | bytes]):
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        response = self.routes[url]
        if isinstance(response, HttpResponse):
            return response
        return HttpResponse(
            status_code=200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            body=response,
        )


class SequenceTransport(FixtureTransport):
    def __init__(self, responses: list[HttpResponse]):
        super().__init__({})
        self.responses = responses

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)


def assert_discovered_item(item: DiscoveredItem, source_id: str) -> None:
    assert isinstance(item, DiscoveredItem)
    assert item.source_id == source_id
    assert item.native_id
    assert item.url
    assert item.title
    assert isinstance(item.metadata, dict)


def assert_raw_document(document: RawDocument, source_id: str) -> None:
    assert isinstance(document, RawDocument)
    assert document.source_id == source_id
    assert document.native_id
    assert document.canonical_url
    assert document.title
    assert document.body
    assert document.content_type
    assert isinstance(document.metadata, dict)


def test_rss_article_discovers_and_fetches_fixture_articles_with_validators():
    feed_url = "https://news.example.test/feed.xml"
    article_url = "https://news.example.test/articles/one"
    transport = FixtureTransport(
        {
            feed_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "application/rss+xml",
                    "ETag": '"feed-v1"',
                    "Last-Modified": "Tue, 15 Sep 2026 10:00:00 GMT",
                },
                body=fixture_bytes("rss.xml"),
            ),
            article_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=fixture_bytes("rss-article-one.html"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "rss_article",
        {
            "source_id": "source.rss.example",
            "feed_url": feed_url,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert isinstance(page, DiscoveryPage)
    assert [item.native_id for item in page.items] == ["article-one", "article-two"]
    assert page.cursor.etag == '"feed-v1"'
    assert page.cursor.last_modified == "Tue, 15 Sep 2026 10:00:00 GMT"
    assert page.cursor.token == "feed-v1"
    assert page.items[0].url == article_url
    assert_discovered_item(page.items[0], "source.rss.example")

    document = connector.fetch(page.items[0])

    assert_raw_document(document, "source.rss.example")
    assert document.body.startswith("<article>")
    assert document.native_id == "article-one"
    assert document.content_type == "text/html"


def test_rss_alias_has_the_same_fixture_behavior_as_rss_article():
    feed_url = "https://news.example.test/feed.xml"
    transport = FixtureTransport(
        {
            feed_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/rss+xml"},
                body=fixture_bytes("rss.xml"),
            )
        }
    )

    connector = ConnectorFactory.create(
        "rss",
        {"source_id": "source.rss.alias", "feed_url": feed_url, "transport": transport},
    )

    page = connector.discover(Cursor())

    assert [item.native_id for item in page.items] == ["article-one", "article-two"]


def test_rss_discovery_sends_conditional_request_headers_and_handles_not_modified():
    feed_url = "https://news.example.test/feed.xml"
    transport = SequenceTransport(
        [
            HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "application/rss+xml",
                    "ETag": '"feed-v1"',
                    "Last-Modified": "Tue, 15 Sep 2026 10:00:00 GMT",
                },
                body=fixture_bytes("rss.xml"),
            ),
            HttpResponse(
                status_code=304,
                headers={
                    "ETag": '"feed-v1"',
                    "Last-Modified": "Tue, 15 Sep 2026 10:00:00 GMT",
                },
                body=b"",
            ),
        ]
    )
    connector = ConnectorFactory.create(
        "rss_article",
        {"source_id": "source.rss.conditional", "feed_url": feed_url, "transport": transport},
    )

    first_page = connector.discover(Cursor())
    second_page = connector.discover(first_page.cursor)

    assert len(second_page.items) == 0
    assert second_page.metadata["not_modified"] is True
    assert transport.calls[1]["headers"]["If-None-Match"] == '"feed-v1"'
    assert (
        transport.calls[1]["headers"]["If-Modified-Since"]
        == "Tue, 15 Sep 2026 10:00:00 GMT"
    )


def test_llms_txt_discovers_markdown_links_and_fetches_one():
    llms_url = "https://docs.example.test/llms.txt"
    guide_url = "https://docs.example.test/guide/getting-started.md"
    transport = FixtureTransport(
        {
            llms_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "ETag": '"llms-v1"',
                },
                body=fixture_bytes("llms.txt"),
            ),
            guide_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown; charset=utf-8"},
                body=fixture_bytes("llms-getting-started.md"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {"source_id": "source.llms.example", "url": llms_url, "transport": transport},
    )

    page = connector.discover(Cursor())
    document = connector.fetch(page.items[0])

    assert [item.title for item in page.items] == ["Getting started", "API reference"]
    assert page.cursor.etag == '"llms-v1"'
    assert_discovered_item(page.items[0], "source.llms.example")
    assert_raw_document(document, "source.llms.example")
    assert document.content_type == "text/markdown"
    assert "install the example package" in document.body


def test_github_tree_discovers_markdown_blobs_and_fetches_raw_content():
    tree_url = "https://api.github.test/repos/example/docs/git/trees/main?recursive=1"
    raw_url = "https://raw.github.test/example/docs/main/README.md"
    transport = FixtureTransport(
        {
            tree_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "application/json",
                    "ETag": '"tree-v1"',
                    "Last-Modified": "Tue, 15 Sep 2026 11:00:00 GMT",
                },
                body=fixture_text("github-tree.json"),
            ),
            raw_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown"},
                body=fixture_bytes("github-readme.md"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "github_tree",
        {
            "source_id": "source.github.example",
            "tree_url": tree_url,
            "repo": "example/docs",
            "ref": "main",
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())
    document = connector.fetch(page.items[0])

    assert [item.native_id for item in page.items] == ["README.md", "docs/guide.md"]
    assert page.cursor.token == "tree-sha-v1"
    assert page.cursor.etag == '"tree-v1"'
    assert page.cursor.last_modified == "Tue, 15 Sep 2026 11:00:00 GMT"
    assert page.items[0].url == raw_url
    assert_raw_document(document, "source.github.example")
    assert document.native_id == "README.md"
    assert document.metadata["sha"] == "blob-readme-v1"
    assert "Example project" in document.body


def test_deepwiki_discovers_linked_pages_and_fetches_page_text():
    root_url = "https://deepwiki.example.test/example/project"
    page_url = "https://deepwiki.example.test/example/project/wiki/Overview"
    transport = FixtureTransport(
        {
            root_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "ETag": '"wiki-v1"',
                },
                body=fixture_bytes("deepwiki-response.txt"),
            ),
            page_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown"},
                body=fixture_bytes("deepwiki-overview.md"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "deepwiki",
        {"source_id": "source.deepwiki.example", "url": root_url, "transport": transport},
    )

    page = connector.discover(Cursor())
    document = connector.fetch(page.items[0])

    assert [item.title for item in page.items] == ["Overview", "Architecture"]
    assert page.cursor.etag == '"wiki-v1"'
    assert_discovered_item(page.items[0], "source.deepwiki.example")
    assert_raw_document(document, "source.deepwiki.example")
    assert document.content_type == "text/markdown"
    assert "wiki overview" in document.body


def test_deepwiki_accepts_nested_module_source_configuration():
    root_url = "https://deepwiki.example.test/example/project"
    tree_url = (
        "https://api.github.com/repos/example/project/git/trees/main?recursive=1"
    )
    transport = FixtureTransport(
        {
            tree_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "application/vnd.github+json",
                    "ETag": '"tree-v1"',
                },
                body=fixture_bytes("github-tree.json"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "deepwiki",
        {
            "source": {"deepwiki": root_url, "github": "example/project"},
            "task": {"source": {"github": "example/project"}},
            "transport": transport,
            "source_id": "framework.example",
        },
    )

    assert connector.healthcheck().status == "healthy"
    assert connector.incremental_class == "revision-native"
    page = connector.discover(Cursor())
    assert [item.native_id for item in page.items] == ["README.md", "docs/guide.md"]
    assert all(item.url.startswith(root_url + "/") for item in page.items)
    assert all(item.remote_revision for item in page.items)


def test_local_import_discovers_fixture_files_with_stable_manifest_cursor():
    root = FIXTURE_ROOT / "local-import"
    connector = ConnectorFactory.create(
        "local_import",
        {"source_id": "source.local.example", "root": root},
    )

    first_page = connector.discover(Cursor())
    second_page = connector.discover(first_page.cursor)
    document = connector.fetch(first_page.items[0])

    assert [item.native_id for item in first_page.items] == ["guide.md", "notes.txt"]
    assert first_page.cursor.token
    assert second_page.items == []
    assert second_page.metadata["unchanged"] is True
    assert_raw_document(document, "source.local.example")
    assert document.url == "local://source.local.example/guide.md"
    assert document.content_type == "text/markdown"


def test_knowledge_source_alias_uses_local_import_without_network():
    connector = ConnectorFactory.create(
        "knowledge_source",
        {
            "source_id": "source.knowledge.example",
            "root": FIXTURE_ROOT / "local-import",
        },
    )

    page = connector.discover(Cursor())

    assert [item.native_id for item in page.items] == ["guide.md", "notes.txt"]
    assert connector.healthcheck().network is False


def test_knowledge_source_alias_infers_llms_from_generic_url():
    llms_url = "https://docs.example.test/llms.txt"
    transport = FixtureTransport(
        {
            llms_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/plain"},
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "knowledge_source",
        {
            "source_id": "source.knowledge.llms",
            "url": llms_url,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert [item.title for item in page.items] == ["Getting started", "API reference"]


def test_html_collection_discovers_filtered_links_and_fetches_article():
    listing_url = "https://blog.example.test/engineering"
    article_url = "https://blog.example.test/engineering/agents"
    transport = FixtureTransport(
        {
            listing_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=fixture_bytes("html-collection-index.html"),
            ),
            article_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=fixture_bytes("html-collection-article.html"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "html_collection",
        {
            "source_id": "source.html.example",
            "listing_url": listing_url,
            "link_prefixes": ["/engineering/"],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())
    document = connector.fetch(page.items[0])

    assert [item.title for item in page.items] == ["Agents at Example"]
    assert page.items[0].url == article_url
    assert_raw_document(document, "source.html.example")
    assert document.content_type == "text/html"
    assert "fixture engineering article" in document.body


def test_static_pages_discovers_declared_pages_and_fetches_reader_url():
    public_url = "https://docs.example.test/guides/agents"
    fetch_url = "https://reader.example.test/guides/agents"
    transport = FixtureTransport(
        {
            fetch_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/plain; charset=utf-8"},
                body="# Agents guide\n\nStatic page fixture.",
            )
        }
    )
    connector = ConnectorFactory.create(
        "static_pages",
        {
            "source_id": "source.static.example",
            "pages": [
                {
                    "id": "agents",
                    "title": "Agents guide",
                    "url": public_url,
                    "fetch_url": fetch_url,
                }
            ],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())
    document = connector.fetch(page.items[0])

    assert [item.native_id for item in page.items] == ["agents"]
    assert page.items[0].url == public_url
    assert document.url == public_url
    assert document.body.startswith("# Agents guide")


def test_static_pages_falls_back_to_public_url_when_reader_url_fails():
    public_url = "https://docs.example.test/guides/agents"
    fetch_url = "https://reader.example.test/guides/agents"
    transport = FixtureTransport(
        {
            fetch_url: HttpResponse(
                status_code=503,
                headers={"Content-Type": "text/plain"},
                body="reader unavailable",
            ),
            public_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body="<html><body>Public page fixture.</body></html>",
            ),
        }
    )
    connector = ConnectorFactory.create(
        "static_pages",
        {
            "source_id": "source.static.fallback",
            "max_retries": 0,
            "pages": [
                {
                    "id": "agents",
                    "title": "Agents guide",
                    "url": public_url,
                    "fetch_url": fetch_url,
                }
            ],
            "transport": transport,
        },
    )

    document = connector.fetch(connector.discover(Cursor()).items[0])

    assert document.body == "<html><body>Public page fixture.</body></html>"
    assert [call["url"] for call in transport.calls] == [fetch_url, public_url]


def test_composite_connector_merges_child_sources_and_dispatches_fetch():
    feed_url = "https://composite.example.test/feed.xml"
    article_url = "https://composite.example.test/article"
    llms_url = "https://composite.example.test/llms.txt"
    guide_url = "https://composite.example.test/guide.md"
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
            article_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/html"},
                body=fixture_bytes("rss-article-one.html"),
            ),
            llms_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/plain"},
                body=fixture_bytes("llms.txt").replace(
                    b"https://docs.example.test/guide/getting-started.md",
                    guide_url.encode("utf-8"),
                ),
            ),
            guide_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/markdown"},
                body=fixture_bytes("llms-getting-started.md"),
            ),
        }
    )
    connector = ConnectorFactory.create(
        "composite",
        {
            "source_id": "source.composite.example",
            "sources": [
                {"adapter": "rss_article", "feed_url": feed_url},
                {"adapter": "llms_txt", "url": llms_url},
            ],
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())
    document = connector.fetch(page.items[0])

    assert len(page.items) == 4
    assert {item.metadata["composite_index"] for item in page.items} == {0, 1}
    assert_raw_document(document, "source.composite.example")


@pytest.mark.parametrize("adapter", REGISTERED_ADAPTERS)
def test_factory_explicitly_covers_every_registered_source_adapter(adapter: str):
    assert adapter in ConnectorFactory.supported_adapters()
    config: dict[str, Any] = {"source_id": f"source.{adapter}"}
    if adapter in {"rss", "rss_article"}:
        config["feed_url"] = "https://news.example.test/feed.xml"
    elif adapter == "llms_txt":
        config["url"] = "https://docs.example.test/llms.txt"
    elif adapter == "github_tree":
        config["tree_url"] = (
            "https://api.github.test/repos/example/docs/git/trees/main?recursive=1"
        )
    elif adapter == "deepwiki":
        config["url"] = "https://deepwiki.example.test/example/project"
    elif adapter == "html_collection":
        config["listing_url"] = "https://blog.example.test/engineering"
    elif adapter == "static_pages":
        config["pages"] = [
            {
                "id": "fixture",
                "title": "Fixture",
                "url": "https://docs.example.test/fixture",
            }
        ]
    elif adapter == "composite":
        config["sources"] = [
            {
                "adapter": "llms_txt",
                "url": "https://docs.example.test/llms.txt",
            }
        ]
    elif adapter in {"local_import", "knowledge_source"}:
        config["root"] = FIXTURE_ROOT / "local-import"
    connector = ConnectorFactory.create(adapter, config)
    assert connector.adapter_name == adapter


@pytest.mark.parametrize("adapter", NON_FETCHING_ADAPTERS)
def test_non_fetching_aliases_are_explicit_non_network_connectors(adapter: str):
    connector = ConnectorFactory.create(
        adapter,
        {"source_id": f"source.{adapter}"},
    )

    health = connector.healthcheck()

    assert health.status == "unsupported"
    assert health.network is False
    assert adapter in health.message
    with pytest.raises(UnsupportedConnectorError, match=adapter):
        connector.discover(Cursor())


def test_connector_http_behavior_is_bounded_and_retries_retry_after_fixture_response():
    llms_url = "https://docs.example.test/llms.txt"
    sleeps: list[float] = []
    transport = SequenceTransport(
        [
            HttpResponse(
                status_code=429,
                headers={"Retry-After": "0"},
                body=b"rate limited",
            ),
            HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/plain"},
                body=fixture_bytes("llms.txt"),
            ),
        ]
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.retry",
            "url": llms_url,
            "transport": transport,
            "max_retries": 1,
            "sleep": sleeps.append,
            "max_response_bytes": 4096,
        },
    )

    page = connector.discover(Cursor())

    assert len(page.items) == 2
    assert sleeps == [0.0]
    assert len(transport.calls) == 2
    assert transport.calls[0]["timeout"] > 0


def test_connector_rejects_oversized_fixture_response_and_bad_content_type():
    llms_url = "https://docs.example.test/llms.txt"
    transport = FixtureTransport(
        {
            llms_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/octet-stream"},
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.safety",
            "url": llms_url,
            "transport": transport,
            "max_response_bytes": 4096,
        },
    )

    with pytest.raises(ValueError, match="content type|response size"):
        connector.discover(Cursor())


def test_connector_handles_headerless_http_response_without_attribute_error():
    llms_url = "https://docs.example.test/llms.txt"
    transport = FixtureTransport(
        {
            llms_url: HttpResponse(
                status_code=200,
                headers={},
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.headerless",
            "url": llms_url,
            "transport": transport,
        },
    )

    assert len(connector.discover(Cursor()).items) == 2


def test_connector_rejects_non_http_scheme_before_transport():
    connector = ConnectorFactory.create(
        "llms_txt",
        {"source_id": "source.file", "url": "file:///etc/hosts"},
    )

    with pytest.raises(ConnectorError, match="http"):
        connector.discover(Cursor())


def test_connector_error_redacts_userinfo_credentials():
    secret_url = "https://user:fixture-secret@docs.example.test/llms.txt"
    transport = FixtureTransport(
        {
            secret_url: HttpResponse(
                status_code=503,
                headers={"Content-Type": "text/plain"},
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.userinfo",
            "url": secret_url,
            "transport": transport,
            "max_retries": 0,
        },
    )

    with pytest.raises(ConnectorError) as error:
        connector.discover(Cursor())

    assert "fixture-secret" not in str(error.value)
    assert "user@" not in str(error.value)


def test_local_import_rejects_oversized_file_before_reading_contents(tmp_path: Path):
    path = tmp_path / "large.md"
    path.write_bytes(b"x" * 128)
    connector = ConnectorFactory.create(
        "local_import",
        {
            "source_id": "source.local.large",
            "root": tmp_path,
            "max_file_bytes": 8,
        },
    )

    with pytest.raises(ValueError, match="exceeds"):
        connector.discover(Cursor())


def test_registry_declared_adapters_have_fixture_matrix_entries():
    from radar_core.contracts import load_registry_document

    registry_root = Path(__file__).parent / "fixtures" / "actual-way-registry"
    document = load_registry_document(registry_root)
    declared = {
        str(task.get("adapter"))
        for module in document["modules"]
        for task in module.get("tasks", [])
        if task.get("adapter")
    }
    knowledge = json.loads(
        (registry_root / "radar/registry/knowledge.json").read_text(encoding="utf-8")
    )
    for source in knowledge.get("sources", []):
        declared.add(str(source.get("adapter")))
        declared.update(
            str(task.get("adapter"))
            for task in source.get("tasks", [])
            if task.get("adapter")
        )
    matrix = json.loads(
        (FIXTURE_ROOT / "adapter-matrix.json").read_text(encoding="utf-8")
    )
    matrix_by_adapter = {item["adapter"]: item for item in matrix["adapters"]}

    assert declared <= set(matrix_by_adapter)
    for adapter in sorted(declared):
        fixture = FIXTURE_ROOT / matrix_by_adapter[adapter]["fixture"]
        assert fixture.exists()


def test_http_errors_redact_query_values_from_fixture_failure_messages():
    secret_url = "https://docs.example.test/llms.txt?access_token=fixture-secret"
    transport = FixtureTransport(
        {
            secret_url: HttpResponse(
                status_code=503,
                headers={"Content-Type": "text/plain"},
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.redacted",
            "url": secret_url,
            "transport": transport,
            "max_retries": 0,
        },
    )

    with pytest.raises(ConnectorError) as error:
        connector.discover(Cursor())

    assert "fixture-secret" not in str(error.value)
    assert "access_token" not in str(error.value)


# --- Task 5: ledger fields on RSS / GitHub tree / llms.txt ---


def test_rss_article_emits_guid_item_id_and_published_remote_revision():
    feed_url = "https://news.example.test/feed.xml"
    transport = FixtureTransport(
        {
            feed_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "application/rss+xml",
                    "ETag": '"feed-v1"',
                },
                body=fixture_bytes("rss.xml"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "rss_article",
        {
            "source_id": "source.rss.ledger",
            "feed_url": feed_url,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "revision-native"
    assert [item.native_id for item in page.items] == ["article-one", "article-two"]
    assert page.items[0].remote_revision == "Tue, 15 Sep 2026 09:00:00 GMT"
    assert page.items[1].remote_revision == "Tue, 15 Sep 2026 08:00:00 GMT"
    assert all(item.has_remote_validator for item in page.items)


def test_rss_feed_without_item_identity_is_unsupported():
    feed_url = "https://news.example.test/anonymous.xml"
    anonymous = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Anon</title>
<item><title>No id</title><description>body</description></item>
</channel></rss>"""
    transport = FixtureTransport(
        {
            feed_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/rss+xml"},
                body=anonymous,
            )
        }
    )
    connector = ConnectorFactory.create(
        "rss_article",
        {
            "source_id": "source.rss.anon",
            "feed_url": feed_url,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "unsupported"
    assert page.items == [] or not any(item.has_remote_validator for item in page.items)


def test_github_tree_items_carry_blob_sha_as_remote_revision():
    tree_url = "https://api.github.test/repos/example/docs/git/trees/main?recursive=1"
    transport = FixtureTransport(
        {
            tree_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "application/json"},
                body=fixture_text("github-tree.json"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "github_tree",
        {
            "source_id": "source.github.ledger",
            "tree_url": tree_url,
            "repo": "example/docs",
            "ref": "main",
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "revision-native"
    assert [item.remote_revision for item in page.items] == [
        "blob-readme-v1",
        "blob-guide-v1",
    ]


def test_llms_txt_entries_use_url_and_optional_etag_as_revision():
    llms_url = "https://docs.example.test/llms.txt"
    transport = FixtureTransport(
        {
            llms_url: HttpResponse(
                status_code=200,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "ETag": '"llms-v1"',
                },
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.ledger",
            "url": llms_url,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "revision-native"
    assert [item.native_id for item in page.items] == [
        "https://docs.example.test/guide/getting-started.md",
        "https://docs.example.test/reference/api.md",
    ]
    assert all(item.remote_revision == '"llms-v1"' for item in page.items)
    assert all(item.remote_etag == '"llms-v1"' for item in page.items)


def test_llms_txt_without_etag_or_last_modified_is_unsupported():
    llms_url = "https://docs.example.test/llms.txt"
    transport = FixtureTransport(
        {
            llms_url: HttpResponse(
                status_code=200,
                headers={"Content-Type": "text/plain"},
                body=fixture_bytes("llms.txt"),
            )
        }
    )
    connector = ConnectorFactory.create(
        "llms_txt",
        {
            "source_id": "source.llms.noetag",
            "url": llms_url,
            "transport": transport,
        },
    )

    page = connector.discover(Cursor())

    assert connector.incremental_class == "unsupported"
    assert len(page.items) == 2
    assert not any(item.has_remote_validator for item in page.items)


def _rss_source(*, max_new_items: int = 10):
    from radar_core.registry import SourceSpec

    return SourceSpec.from_payload(
        {
            "schema": "radar-content-contract/v1/source",
            "id": "source.rss.news",
            "kind": "source",
            "source_type": "rss_article",
            "adapter": "rss_article",
            "name": "RSS News",
            "locator": "https://news.example.test/feed.xml",
            "schedule": {
                "enabled": True,
                "timezone": "UTC",
                "cron": "0 * * * *",
                "max_new_items": max_new_items,
            },
            "output_root": "frontend/news",
            "translation_profile": "prose/v1",
            "enabled": True,
        }
    )


def _rss_run_context(state, connector, *, run_id: str = "run-1"):
    from datetime import datetime, timezone

    from radar_core.pipeline import RunContext
    from radar_core.translation.base import TranslationRequest, TranslationResponse

    class _Router:
        def translate(self, request: TranslationRequest) -> TranslationResponse:
            return TranslationResponse(
                translated_text=f"译文：{request.text[:40]}",
                provider="fake",
                model="fake-model",
                metadata={"provider": "fake"},
            )

    return RunContext(
        state=state,
        run_id=run_id,
        target_locales=("zh-CN",),
        connector_factory=lambda _source: connector,
        router=_Router(),
        now=datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc),
        mode="incremental",
    )


def test_rss_304_through_run_source_fetches_zero_articles(tmp_path: Path):
    from radar_core.pipeline import run_source
    from radar_core.storage import StateStore

    feed_url = "https://news.example.test/feed.xml"
    article_one = "https://news.example.test/articles/one"
    article_two = "https://news.example.test/articles/two"
    feed_200 = HttpResponse(
        status_code=200,
        headers={
            "Content-Type": "application/rss+xml",
            "ETag": '"feed-v1"',
            "Last-Modified": "Tue, 15 Sep 2026 10:00:00 GMT",
        },
        body=fixture_bytes("rss.xml"),
    )
    feed_304 = HttpResponse(
        status_code=304,
        headers={
            "ETag": '"feed-v1"',
            "Last-Modified": "Tue, 15 Sep 2026 10:00:00 GMT",
        },
        body=b"",
    )
    article_body = HttpResponse(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=fixture_bytes("rss-article-one.html"),
    )
    routes = {
        feed_url: feed_200,
        article_one: article_body,
        article_two: article_body,
    }

    class _HybridTransport(SequenceTransport):
        def request(self, method, url, *, headers, timeout):
            if url == feed_url and self.responses:
                return super().request(method, url, headers=headers, timeout=timeout)
            response = routes[url]
            self.calls.append(
                {
                    "method": method,
                    "url": url,
                    "headers": dict(headers),
                    "timeout": timeout,
                }
            )
            return response

    transport = _HybridTransport([feed_200, feed_304])
    connector = ConnectorFactory.create(
        "rss_article",
        {
            "source_id": "source.rss.news",
            "feed_url": feed_url,
            "transport": transport,
        },
    )
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _rss_source()

    first = run_source(source, _rss_run_context(state, connector, run_id="r1"))
    assert first.run_kind == "baseline_only"
    assert first.fetched == 0

    for item_id, revision, url in (
        ("article-one", "Tue, 15 Sep 2026 09:00:00 GMT", article_one),
        ("article-two", "Tue, 15 Sep 2026 08:00:00 GMT", article_two),
    ):
        state.upsert_source_item(
            {
                "source_id": "source.rss.news",
                "item_id": item_id,
                "canonical_url": url,
                "remote_revision": revision,
                "fetched_revision": revision,
                "status": "fetched",
            }
        )

    article_gets_before = [
        call for call in transport.calls if call["url"] in {article_one, article_two}
    ]
    assert article_gets_before == []

    second = run_source(source, _rss_run_context(state, connector, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.fetched == 0
    article_gets = [
        call for call in transport.calls if call["url"] in {article_one, article_two}
    ]
    assert article_gets == []
    state.close()


def test_rss_new_guid_is_only_fetch_after_baseline(tmp_path: Path):
    from radar_core.pipeline import run_source
    from radar_core.storage import StateStore

    feed_url = "https://news.example.test/feed.xml"
    article_one = "https://news.example.test/articles/one"
    article_two = "https://news.example.test/articles/two"
    article_three = "https://news.example.test/articles/three"

    feed_v1 = fixture_bytes("rss.xml")
    feed_v2 = feed_v1.replace(
        b"</channel>",
        b"""  <item>
      <title>Third article</title>
      <guid isPermaLink="false">article-three</guid>
      <link>https://news.example.test/articles/three</link>
      <pubDate>Tue, 15 Sep 2026 11:00:00 GMT</pubDate>
      <description>Third article summary.</description>
    </item>
</channel>""",
    )
    article_html = HttpResponse(
        status_code=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=fixture_bytes("rss-article-one.html"),
    )

    class _RssTransport(FixtureTransport):
        def __init__(self) -> None:
            super().__init__({})
            self.feed_bodies = [feed_v1, feed_v2]
            self.routes = {
                article_one: article_html,
                article_two: article_html,
                article_three: article_html,
            }

        def request(self, method, url, *, headers, timeout):
            self.calls.append(
                {
                    "method": method,
                    "url": url,
                    "headers": dict(headers),
                    "timeout": timeout,
                }
            )
            if url == feed_url:
                body = self.feed_bodies.pop(0)
                return HttpResponse(
                    status_code=200,
                    headers={
                        "Content-Type": "application/rss+xml",
                        "ETag": f'"feed-v{3 - len(self.feed_bodies)}"',
                    },
                    body=body,
                )
            return self.routes[url]

    transport = _RssTransport()
    connector = ConnectorFactory.create(
        "rss_article",
        {
            "source_id": "source.rss.news",
            "feed_url": feed_url,
            "transport": transport,
        },
    )
    state = StateStore.open(tmp_path / "state.sqlite3")
    source = _rss_source()

    first = run_source(source, _rss_run_context(state, connector, run_id="r1"))
    assert first.run_kind == "baseline_only"
    assert first.fetched == 0

    for item_id, revision, url in (
        ("article-one", "Tue, 15 Sep 2026 09:00:00 GMT", article_one),
        ("article-two", "Tue, 15 Sep 2026 08:00:00 GMT", article_two),
    ):
        state.upsert_source_item(
            {
                "source_id": "source.rss.news",
                "item_id": item_id,
                "canonical_url": url,
                "remote_revision": revision,
                "fetched_revision": revision,
                "status": "fetched",
            }
        )

    second = run_source(source, _rss_run_context(state, connector, run_id="r2"))

    assert second.run_kind == "incremental"
    assert second.fetched == 1
    assert second.selected == 1
    article_gets = [
        call["url"]
        for call in transport.calls
        if call["url"] in {article_one, article_two, article_three}
    ]
    assert article_gets == [article_three]
    state.close()
