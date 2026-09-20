from __future__ import annotations

from radar_core.hashing import sha256_text
from radar_core.ids import (
    canonicalize_url,
    content_id_for,
    revision_id_for,
    translation_key,
)


def test_canonicalize_url_removes_tracking_and_fragment_but_keeps_content_query():
    assert (
        canonicalize_url(
            " HTTPS://Example.COM:443/docs?tag=second&utm_source=news&tag=first#section "
        )
        == "https://example.com/docs?tag=second&tag=first"
    )


def test_content_id_prefers_native_id_over_url_and_slug():
    native = content_id_for(
        "source.demo",
        native_id="42",
        canonical_url="https://example.com/changed",
        slug="changed",
    )
    same_native = content_id_for(
        "source.demo",
        native_id="42",
        canonical_url="https://example.com/other",
        slug="other",
    )

    assert native == same_native
    assert native.startswith("content_")


def test_content_id_uses_canonical_url_then_stable_slug():
    assert content_id_for(
        "source.demo",
        canonical_url="https://example.com/a#fragment",
    ) == content_id_for("source.demo", canonical_url="https://example.com/a")
    assert content_id_for("source.demo", slug="Hello World") == content_id_for(
        "source.demo", slug="hello-world"
    )


def test_revision_and_translation_keys_change_when_inputs_change():
    first_revision = revision_id_for("paragraph one", "normalizer/v1")
    second_revision = revision_id_for("paragraph two", "normalizer/v1")
    first_key = translation_key(
        "content-a",
        first_revision,
        "zh-CN",
        "prose/v1",
        "policy/v1",
    )
    second_key = translation_key(
        "content-a",
        first_revision,
        "zh-CN",
        "prose/v1",
        "policy/v2",
    )

    assert first_revision != second_revision
    assert first_key != second_key
    assert first_revision.startswith("revision_")
    assert first_key.startswith("translation_")
    assert sha256_text("paragraph one") != sha256_text("paragraph two")


def test_revision_and_translation_inputs_are_structured_not_delimited_strings():
    assert revision_id_for("a\nb", "normalizer/v1") != revision_id_for(
        "b", "normalizer/v1\na"
    )
    assert translation_key("content", "revision\nlocale", "profile", "policy", "") != translation_key(
        "content", "revision", "locale\nprofile", "policy", ""
    )
