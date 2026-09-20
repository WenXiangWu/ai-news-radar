from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .ids import canonicalize_url, content_id_for


NORMALIZER_VERSION = "normalizer/v1"
_URL_RE = re.compile(r"https?://[^\s<>\])}]+")
_UNSTABLE_QUERY_KEYS = {
    "_",
    "build",
    "cache",
    "cachebust",
    "cb",
    "hash",
    "t",
    "timestamp",
    "v",
    "version",
}
_VOLATILE_METADATA_KEYS = {
    "cursor",
    "etag",
    "fetch_time",
    "fetched_at",
    "last_modified",
    "request_id",
    "retrieved_at",
}


@dataclass(frozen=True)
class NormalizedDocument:
    source_id: str
    content_id: str
    native_id: Optional[str]
    canonical_url: str
    title: str
    body: str
    published_at: Optional[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    normalizer_version: str = NORMALIZER_VERSION
    source_hash: str = ""


def _value(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _stable_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parts = urlsplit(canonicalize_url(value))
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _UNSTABLE_QUERY_KEYS
    ]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            "",
        )
    )


def _replace_urls(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in ".,;:!?":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        return _stable_url(raw) + trailing

    return _URL_RE.sub(replace, text)


def _normalize_body(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _replace_urls(text)
    lines = [line.rstrip() for line in text.split("\n")]
    normalized: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                normalized.append("")
            blank = True
            continue
        normalized.append(line)
        blank = False
    return "\n".join(normalized).strip()


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        if str(key).lower() not in _VOLATILE_METADATA_KEYS
    }


def _source_hash(
    *,
    title: str,
    body: str,
    published_at: Optional[str],
    metadata: Mapping[str, Any],
) -> str:
    stable = {
        "title": title,
        "body": body,
        "published_at": published_at,
        "metadata": dict(metadata),
    }
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_document(raw: Any, profile: str) -> NormalizedDocument:
    source_id = str(_value(raw, "source_id", "") or "").strip()
    if not source_id:
        raise ValueError("raw document requires source_id")
    native_id_value = _value(raw, "native_id")
    native_id = str(native_id_value).strip() if native_id_value else None
    canonical_url = _stable_url(str(_value(raw, "canonical_url", "") or ""))
    title = " ".join(str(_value(raw, "title", "") or "").split())
    body = _normalize_body(_value(raw, "body", ""))
    published_value = _value(raw, "published_at")
    published_at = str(published_value).strip() if published_value else None
    metadata = _normalize_metadata(_value(raw, "metadata", {}))
    slug = str(_value(raw, "slug", "") or "").strip() or title
    content_id = content_id_for(
        source_id,
        native_id=native_id,
        canonical_url=canonical_url,
        slug=slug,
    )
    source_hash = _source_hash(
        title=title,
        body=body,
        published_at=published_at,
        metadata=metadata,
    )
    return NormalizedDocument(
        source_id=source_id,
        content_id=content_id,
        native_id=native_id,
        canonical_url=canonical_url,
        title=title,
        body=body,
        published_at=published_at,
        metadata={
            **metadata,
            "profile": str(profile),
        },
        source_hash=source_hash,
    )


def normalized_hash(document: NormalizedDocument) -> str:
    payload = asdict(document)
    payload.pop("source_hash", None)
    payload["metadata"] = {
        key: value
        for key, value in payload["metadata"].items()
        if key != "profile"
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
