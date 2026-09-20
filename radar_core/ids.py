from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .hashing import sha256_text


_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_source",
}
_TRACKING_PREFIXES = ("utm_",)


def canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not hostname:
        return raw.rstrip("/")
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = hostname
    if parts.username:
        netloc = f"{parts.username}:{parts.password or ''}@{netloc}"
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_KEYS
        and not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    query.sort()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    normalized = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE)
    return normalized.strip("-_")


def _stable_basis(
    source_id: str,
    native_id: str | None,
    canonical_url: str | None,
    slug: str | None,
) -> str:
    source = _slugify(source_id)
    if native_id and str(native_id).strip():
        return f"{source}:native:{str(native_id).strip()}"
    if canonical_url and canonicalize_url(canonical_url):
        return f"{source}:url:{canonicalize_url(canonical_url)}"
    if slug and _slugify(slug):
        return f"{source}:slug:{_slugify(slug)}"
    raise ValueError("content identity requires native_id, canonical_url, or slug")


def content_id_for(
    source_id: str,
    native_id: str | None = None,
    canonical_url: str | None = None,
    slug: str | None = None,
) -> str:
    return "content_" + sha256_text(
        _stable_basis(source_id, native_id, canonical_url, slug)
    )[:32]


def revision_id_for(normalized_text: str, normalizer_version: str) -> str:
    return "revision_" + sha256_text(
        f"{normalizer_version}\n{normalized_text}"
    )[:32]


def translation_key(
    content_id: str,
    revision_id: str,
    locale: str,
    profile: str,
    policy: str,
) -> str:
    raw = "\n".join(
        (
            str(content_id),
            str(revision_id),
            str(locale),
            str(profile),
            str(policy),
        )
    )
    return "translation_" + sha256_text(raw)[:32]
