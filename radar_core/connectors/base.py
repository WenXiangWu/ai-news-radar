from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ..hashing import sha256_structured, sha256_text
from ..ids import canonicalize_url


DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_RETRIES = 2
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DEFAULT_USER_AGENT = "ai-news-radar/connector-sdk"


class ConnectorError(ValueError):
    """A safe, user-facing connector failure."""


class UnsupportedConnectorError(ConnectorError):
    """Raised when an adapter is registered but outside this connector scope."""


@dataclass(frozen=True)
class Cursor:
    token: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: Cursor | Mapping[str, Any] | str | None) -> "Cursor":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(token=value)
        if isinstance(value, Mapping):
            metadata = value.get("metadata")
            return cls(
                token=_optional_string(value.get("token") or value.get("cursor")),
                etag=_optional_string(value.get("etag")),
                last_modified=_optional_string(value.get("last_modified")),
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        raise TypeError(f"unsupported cursor value: {type(value).__name__}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "metadata": dict(self.metadata),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


@dataclass(frozen=True)
class DiscoveredItem:
    source_id: str
    native_id: str
    url: str
    title: str
    published_at: str | None = None
    content_type: str = "text/plain"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def canonical_url(self) -> str:
        if self.url.startswith(("local://", "file://")):
            return self.url
        return canonicalize_url(self.url)

    @property
    def source_native_id(self) -> str:
        return self.native_id


@dataclass(frozen=True)
class DiscoveryPage:
    items: list[DiscoveredItem]
    cursor: Cursor = field(default_factory=Cursor)
    has_more: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def next_cursor(self) -> Cursor:
        return self.cursor


@dataclass(frozen=True)
class RawDocument:
    source_id: str
    native_id: str
    url: str
    title: str
    text: str
    content_type: str
    published_at: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def canonical_url(self) -> str:
        if self.url.startswith(("local://", "file://")):
            return self.url
        return canonicalize_url(self.url)

    @property
    def source_native_id(self) -> str:
        return self.native_id

    @property
    def body(self) -> str:
        return self.text

    @property
    def content(self) -> str:
        return self.text


@dataclass(frozen=True)
class HealthStatus:
    adapter: str
    status: str
    message: str = ""
    network: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"healthy", "degraded"}


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | str = b""

    @property
    def content_length(self) -> int:
        if isinstance(self.body, bytes):
            return len(self.body)
        return len(self.body.encode("utf-8"))

    @property
    def text(self) -> str:
        if isinstance(self.body, bytes):
            return self.body.decode("utf-8", errors="replace")
        return str(self.body)

    @property
    def content_type(self) -> str:
        value = _header(self.headers, "Content-Type")
        return value.split(";", 1)[0].strip().lower()


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        ...


class UrllibTransport:
    """Small default transport; tests and callers can inject a deterministic one."""

    def __init__(self, max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
        self.max_response_bytes = max(1, int(max_response_bytes))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read(self.max_response_bytes + 1)
                response_headers = {
                    str(key): str(value) for key, value in response.headers.items()
                }
                return HttpResponse(
                    status_code=int(response.status),
                    headers=response_headers,
                    body=body,
                )
        except HTTPError as exc:
            body = exc.read(self.max_response_bytes + 1)
            response_headers = {
                str(key): str(value) for key, value in exc.headers.items()
            }
            return HttpResponse(
                status_code=int(exc.code),
                headers=response_headers,
                body=body,
            )
        except (OSError, URLError) as exc:
            raise ConnectorError(f"network request failed: {type(exc).__name__}") from exc


class Connector(Protocol):
    adapter_name: str

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        ...

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        ...

    def healthcheck(self) -> HealthStatus:
        ...


class BaseConnector:
    adapter_name = "connector"
    network = True

    def __init__(self, config: Mapping[str, Any] | None = None):
        self.config = dict(config or {})
        self.adapter_name = str(
            self.config.get("adapter_name") or self.adapter_name
        )
        self.source_id = str(
            self.config.get("source_id")
            or self.config.get("id")
            or self.adapter_name
        )

    def healthcheck(self) -> HealthStatus:
        configuration_error = getattr(self, "_configuration_error", None)
        if configuration_error:
            return HealthStatus(
                adapter=self.adapter_name,
                status="degraded",
                message=f"{self.adapter_name}: {configuration_error}",
                network=self.network,
                details={"reason": configuration_error},
            )
        return HealthStatus(
            adapter=self.adapter_name,
            status="healthy",
            message=f"{self.adapter_name} connector ready",
            network=self.network,
        )


class HttpConnector(BaseConnector):
    network = True

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        self.timeout = _positive_float(
            self.config.get("timeout_seconds", self.config.get("timeout")),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.max_response_bytes = max(
            1,
            int(
                self.config.get(
                    "max_response_bytes",
                    DEFAULT_MAX_RESPONSE_BYTES,
                )
            ),
        )
        self.max_retries = max(
            0,
            int(self.config.get("max_retries", DEFAULT_MAX_RETRIES)),
        )
        self.sleep: Callable[[float], Any] = self.config.get("sleep") or time.sleep
        transport = self.config.get("transport")
        self.transport: Transport | Callable[..., HttpResponse] = transport or UrllibTransport(
            self.max_response_bytes
        )

    def _request(
        self,
        url: str,
        cursor: Cursor | Mapping[str, Any] | str | None = None,
        *,
        expected_content_types: Sequence[str] = (),
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        current_cursor = Cursor.coerce(cursor)
        headers = {
            "Accept": ", ".join(expected_content_types) or "*/*",
            "User-Agent": str(
                self.config.get("user_agent") or DEFAULT_USER_AGENT
            ),
        }
        if current_cursor.etag:
            headers["If-None-Match"] = current_cursor.etag
        if current_cursor.last_modified:
            headers["If-Modified-Since"] = current_cursor.last_modified
        if extra_headers:
            headers.update({str(key): str(value) for key, value in extra_headers.items()})

        for attempt in range(self.max_retries + 1):
            response = self._call_transport("GET", url, headers)
            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self.max_retries
            ):
                delay = _retry_after_seconds(response.headers)
                self.sleep(delay)
                continue
            if response.status_code == 304:
                return response
            if response.status_code < 200 or response.status_code >= 300:
                raise ConnectorError(
                    f"{self.adapter_name} request failed with HTTP "
                    f"{response.status_code} for {_redact_url(url)}"
                )
            self._validate_response(response, expected_content_types, url)
            return response
        raise ConnectorError(f"{self.adapter_name} request failed for {_redact_url(url)}")

    def _call_transport(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
    ) -> HttpResponse:
        try:
            if hasattr(self.transport, "request"):
                response = self.transport.request(
                    method,
                    url,
                    headers=headers,
                    timeout=self.timeout,
                )
            else:
                response = self.transport(
                    method,
                    url,
                    headers=headers,
                    timeout=self.timeout,
                )
        except ConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(
                f"{self.adapter_name} transport failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(response, HttpResponse):
            raise ConnectorError(
                f"{self.adapter_name} transport returned an invalid response"
            )
        return response

    def _validate_response(
        self,
        response: HttpResponse,
        expected_content_types: Sequence[str],
        url: str,
    ) -> None:
        if response.content_length > self.max_response_bytes:
            raise ConnectorError(
                f"{self.adapter_name} response size exceeds "
                f"{self.max_response_bytes} bytes for {_redact_url(url)}"
            )
        content_type = response.content_type
        expected = {
            str(value).split(";", 1)[0].strip().lower()
            for value in expected_content_types
        }
        if content_type and expected and not _content_type_matches(content_type, expected):
            raise ConnectorError(
                f"{self.adapter_name} response content type {content_type!r} "
                f"is not allowed for {_redact_url(url)}"
            )

    def _cursor(
        self,
        response: HttpResponse,
        *,
        token: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        body: str | None = None,
    ) -> Cursor:
        etag = _header(response.headers, "ETag")
        last_modified = _header(response.headers, "Last-Modified")
        stable_token = (
            token
            or _strip_etag(etag)
            or last_modified
            or (sha256_text(body)[:32] if body is not None else None)
        )
        return Cursor(
            token=stable_token,
            etag=etag,
            last_modified=last_modified,
            metadata=dict(metadata or {}),
        )


class UnsupportedConnector(BaseConnector):
    network = False

    def __init__(self, adapter_name: str, reason: str):
        super().__init__({"adapter_name": adapter_name, "source_id": adapter_name})
        self.reason = reason

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        raise UnsupportedConnectorError(
            f"{self.adapter_name}: {self.reason}"
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        raise UnsupportedConnectorError(
            f"{self.adapter_name}: {self.reason}"
        )

    def healthcheck(self) -> HealthStatus:
        return HealthStatus(
            adapter=self.adapter_name,
            status="unsupported",
            message=f"{self.adapter_name}: {self.reason}",
            network=False,
            details={"reason": self.reason},
        )


class ConnectorFactory:
    _ADAPTERS = {
        "rss": "rss_article",
        "rss_article": "rss_article",
        "llms_txt": "llms_txt",
        "github_tree": "github_tree",
        "deepwiki": "deepwiki",
        "knowledge_source": "knowledge_source",
        "local_import": "local_import",
        "markdown_google": "unsupported",
        "coding_tools_catalog": "unsupported",
        "qdrant_editorial_catalog": "unsupported",
        "framework_hubs": "unsupported",
        "manual_editorial": "unsupported",
    }
    _UNSUPPORTED_REASONS = {
        "markdown_google": "translation is owned by TranslationRouter, not a source fetch",
        "coding_tools_catalog": "catalog indexing is outside the connector fetch scope",
        "qdrant_editorial_catalog": "editorial catalog indexing is outside the connector fetch scope",
        "framework_hubs": "framework hub indexing is outside the connector fetch scope",
        "manual_editorial": "manual editorial content has no network source",
    }

    @classmethod
    def supported_adapters(cls) -> tuple[str, ...]:
        return tuple(cls._ADAPTERS)

    @classmethod
    def create(
        cls,
        adapter: str,
        config: dict[str, Any],
    ) -> Connector:
        requested = str(adapter or "").strip()
        if requested not in cls._ADAPTERS:
            raise ValueError(f"unsupported connector adapter: {requested}")
        settings = dict(config or {})
        settings["adapter_name"] = requested
        target = cls._ADAPTERS[requested]
        if target == "unsupported":
            return UnsupportedConnector(
                requested,
                cls._UNSUPPORTED_REASONS[requested],
            )
        if target == "knowledge_source":
            selected = cls._knowledge_source_target(settings)
            if selected is None:
                return UnsupportedConnector(
                    requested,
                    "requires local root/files or an explicit first-party source format",
                )
            target = selected
        if target == "rss_article":
            from .rss_article import RSSArticleConnector

            return RSSArticleConnector(settings)
        if target == "llms_txt":
            from .llms_txt import LLMSTxtConnector

            return LLMSTxtConnector(settings)
        if target == "github_tree":
            from .github_tree import GitHubTreeConnector

            return GitHubTreeConnector(settings)
        if target == "deepwiki":
            from .deepwiki import DeepWikiConnector

            return DeepWikiConnector(settings)
        if target == "local_import":
            from .local_import import LocalImportConnector

            return LocalImportConnector(settings)
        raise ValueError(f"connector target is not implemented: {target}")

    @staticmethod
    def _knowledge_source_target(config: Mapping[str, Any]) -> str | None:
        if any(config.get(key) for key in ("root", "root_dir", "path", "files")):
            return "local_import"
        if config.get("feed_url") or str(config.get("format") or "").lower() in {
            "rss",
            "atom",
        }:
            return "rss_article"
        if config.get("llms_url") or str(config.get("format") or "").lower() == "llms_txt":
            return "llms_txt"
        if config.get("tree_url") or config.get("repo"):
            return "github_tree"
        if config.get("deepwiki_url") or str(config.get("format") or "").lower() == "deepwiki":
            return "deepwiki"
        raw_url = str(config.get("url") or config.get("locator") or "").strip()
        lowered_url = raw_url.lower()
        if lowered_url.endswith("/llms.txt") or lowered_url.endswith("llms.txt"):
            return "llms_txt"
        if "deepwiki" in lowered_url:
            return "deepwiki"
        if "/git/trees/" in lowered_url or (
            "api.github.com" in lowered_url and "/trees/" in lowered_url
        ):
            return "github_tree"
        if (
            lowered_url.endswith(".xml")
            or lowered_url.endswith(".rss")
            or "/feed" in lowered_url
            or "rss" in lowered_url
        ):
            return "rss_article"
        return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == expected:
            return str(value)
    return None


def _strip_etag(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip('"') or None


def _retry_after_seconds(headers: Mapping[str, Any]) -> float:
    value = _header(headers, "Retry-After")
    if value:
        try:
            return max(0.0, min(float(value), 60.0))
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=datetime.now().astimezone().tzinfo)
                return max(0.0, min((target - datetime.now(target.tzinfo)).total_seconds(), 60.0))
            except (TypeError, ValueError, OverflowError):
                pass
    return 0.0


def _content_type_matches(content_type: str, expected: set[str]) -> bool:
    if content_type in expected:
        return True
    if content_type.endswith("+xml") and "application/xml" in expected:
        return True
    if content_type.endswith("+json") and "application/json" in expected:
        return True
    if content_type == "text/html" and "application/xhtml+xml" in expected:
        return True
    return False


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed > 0 else default


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "<invalid-url>"
    if not parts.scheme or not parts.netloc:
        return "<local-url>"
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"


def content_type_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".mdx": "text/markdown",
        ".json": "application/json",
        ".xml": "application/xml",
        ".rss": "application/rss+xml",
        ".txt": "text/plain",
        ".rst": "text/plain",
    }.get(suffix, "text/plain")


def stable_manifest_token(manifest: Any) -> str:
    return sha256_structured(manifest)[:32]


def parse_json_object(text: str, adapter_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"{adapter_name} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ConnectorError(f"{adapter_name} returned a JSON object")
    return payload
