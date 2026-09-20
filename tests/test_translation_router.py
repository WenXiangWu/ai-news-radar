from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import pytest


SOURCE_TEXT = "OpenAI launches a new coding agent"
DEEPSEEK_TEXT = "OpenAI 推出新的编程智能体"
GOOGLE_TEXT = "OpenAI 推出了新的编码代理"


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    json_error: Exception | None = None

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeTransport:
    def __init__(
        self,
        *,
        post_response: FakeResponse | None = None,
        get_response: FakeResponse | None = None,
        post_error: Exception | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self.post_response = post_response
        self.get_response = get_response
        self.post_error = post_error
        self.get_error = get_error
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if self.post_error is not None:
            raise self.post_error
        if self.post_response is None:
            raise AssertionError("unexpected DeepSeek request")
        return self.post_response

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        if self.get_error is not None:
            raise self.get_error
        if self.get_response is None:
            raise AssertionError("unexpected Google request")
        return self.get_response


def make_request(**overrides: Any) -> Any:
    try:
        from radar_core.translation.base import TranslationRequest
    except ModuleNotFoundError as exc:
        pytest.fail(f"translation package is missing: {exc}")
    values = {
        "text": SOURCE_TEXT,
        "source_locale": "en",
        "target_locale": "zh-CN",
        "translation_profile": "title/v1",
        "prompt_version": "prompt/v1",
        "policy_version": "policy/v1",
    }
    values.update(overrides)
    return TranslationRequest(**values)


def make_router(
    *,
    deepseek_transport: FakeTransport | None = None,
    google_transport: FakeTransport | None = None,
    deepseek_api_key: str | None = "deepseek-secret",
) -> Any:
    try:
        from radar_core.translation.deepseek import DeepSeekProvider
        from radar_core.translation.google import GoogleProvider
        from radar_core.translation.router import TranslationRouter
    except ModuleNotFoundError as exc:
        pytest.fail(f"translation package is missing: {exc}")
    deepseek = DeepSeekProvider(
        api_key=deepseek_api_key,
        base_url="https://deepseek.test",
        model="deepseek-test",
        transport=deepseek_transport or FakeTransport(),
        timeout_seconds=3,
    )
    google = GoogleProvider(
        endpoint="https://google.test/translate",
        transport=google_transport or FakeTransport(),
        timeout_seconds=4,
    )
    return TranslationRouter(deepseek=deepseek, google=google)


def deepseek_payload(text: str = DEEPSEEK_TEXT) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


def google_payload(text: str = GOOGLE_TEXT) -> list[Any]:
    return [[[text, SOURCE_TEXT]]]


def test_provider_order_is_stable_and_deepseek_first() -> None:
    router = make_router()

    assert router.provider_order() == ("deepseek", "google")


def test_deepseek_success_wins_without_calling_google() -> None:
    deepseek_transport = FakeTransport(
        post_response=FakeResponse(200, deepseek_payload())
    )
    google_transport = FakeTransport(
        get_response=FakeResponse(200, google_payload())
    )
    router = make_router(
        deepseek_transport=deepseek_transport,
        google_transport=google_transport,
    )

    response = router.translate(make_request())

    assert response.translated_text == DEEPSEEK_TEXT
    assert response.provider == "deepseek"
    assert response.fallback_reason is None
    assert len(response.telemetry) == 1
    assert response.telemetry[0].provider == "deepseek"
    assert response.telemetry[0].attempt == 1
    assert not google_transport.get_calls


def test_missing_deepseek_key_falls_back_to_google_without_http_call() -> None:
    deepseek_transport = FakeTransport()
    google_transport = FakeTransport(get_response=FakeResponse(200, google_payload()))
    router = make_router(
        deepseek_transport=deepseek_transport,
        google_transport=google_transport,
        deepseek_api_key=None,
    )

    response = router.translate(make_request())

    assert response.translated_text == GOOGLE_TEXT
    assert response.provider == "google"
    assert response.fallback_reason == "deepseek:missing_api_key"
    assert not deepseek_transport.post_calls
    assert len(response.telemetry) == 2
    assert response.telemetry[0].provider == "deepseek"
    assert response.telemetry[0].reason == "missing_api_key"
    assert response.telemetry[0].error_category == "permanent"
    assert response.telemetry[1].provider == "google"


@pytest.mark.parametrize(
    ("deepseek_transport", "expected_reason", "expected_category"),
    [
        (
            FakeTransport(post_error=TimeoutError("request timed out")),
            "timeout",
            "retryable",
        ),
        (
            FakeTransport(post_response=FakeResponse(429, {"error": "rate limited"})),
            "http_429",
            "retryable",
        ),
        (
            FakeTransport(
                post_response=FakeResponse(
                    200,
                    json_error=ValueError("not json"),
                )
            ),
            "malformed_json",
            "invalid_output",
        ),
        (
            FakeTransport(post_response=FakeResponse(200, deepseek_payload("Sorry, I cannot translate this"))),
            "refusal",
            "invalid_output",
        ),
        (
            FakeTransport(
                post_response=FakeResponse(
                    200,
                    deepseek_payload("I cannot process this link"),
                )
            ),
            "refusal",
            "invalid_output",
        ),
        (
            FakeTransport(post_response=FakeResponse(200, deepseek_payload("前体"))),
            "invalid_output",
            "invalid_output",
        ),
        (
            FakeTransport(
                post_response=FakeResponse(200, deepseek_payload("Precursor"))
            ),
            "invalid_output",
            "invalid_output",
        ),
    ],
)
def test_deepseek_failures_fall_back_to_google(
    deepseek_transport: FakeTransport,
    expected_reason: str,
    expected_category: str,
) -> None:
    google_transport = FakeTransport(get_response=FakeResponse(200, google_payload()))
    router = make_router(
        deepseek_transport=deepseek_transport,
        google_transport=google_transport,
    )

    response = router.translate(make_request())

    assert response.translated_text == GOOGLE_TEXT
    assert response.provider == "google"
    assert response.fallback_reason == f"deepseek:{expected_reason}"
    failed_attempt = response.telemetry[0]
    assert failed_attempt.reason == expected_reason
    assert failed_attempt.error_category == expected_category
    assert response.telemetry[1].provider == "google"


def test_google_failure_returns_stable_failure_metadata() -> None:
    deepseek_transport = FakeTransport(
        post_response=FakeResponse(429, {"error": "rate limited"})
    )
    google_transport = FakeTransport(get_error=TimeoutError("request timed out"))
    router = make_router(
        deepseek_transport=deepseek_transport,
        google_transport=google_transport,
    )
    request = make_request()

    first = router.translate(request)
    second = router.translate(request)

    assert first.translated_text is None
    assert first.provider is None
    assert first.fallback_reason == "deepseek:http_429"
    assert first.reason == "google:timeout"
    stable_metadata = (
        "provider",
        "model",
        "prompt_version",
        "policy_version",
        "input_hash",
        "output_hash",
        "attempt",
        "fallback_reason",
    )
    assert {
        key: first.metadata[key] for key in stable_metadata
    } == {
        key: second.metadata[key] for key in stable_metadata
    }
    assert [
        (item.provider, item.model, item.reason, item.error_category, item.attempt)
        for item in first.telemetry
    ] == [
        (item.provider, item.model, item.reason, item.error_category, item.attempt)
        for item in second.telemetry
    ]
    assert first.metadata["input_hash"] == hashlib.sha256(
        SOURCE_TEXT.encode("utf-8")
    ).hexdigest()
    assert first.metadata["output_hash"] is None
    assert first.metadata["prompt_version"] == "prompt/v1"
    assert first.metadata["policy_version"] == "policy/v1"
    assert [item.attempt for item in first.telemetry] == [1, 2]


def test_telemetry_is_redacted_and_contains_provider_metadata() -> None:
    secret = "deepseek-secret"
    deepseek_transport = FakeTransport(
        post_response=FakeResponse(200, deepseek_payload())
    )
    router = make_router(
        deepseek_transport=deepseek_transport,
        deepseek_api_key=secret,
    )

    response = router.translate(make_request())

    assert secret not in repr(response)
    assert response.metadata["provider"] == "deepseek"
    assert response.metadata["model"] == "deepseek-test"
    assert response.metadata["prompt_version"] == "prompt/v1"
    assert response.metadata["policy_version"] == "policy/v1"
    assert response.metadata["input_hash"] == hashlib.sha256(
        SOURCE_TEXT.encode("utf-8")
    ).hexdigest()
    assert response.metadata["output_hash"] == hashlib.sha256(
        DEEPSEEK_TEXT.encode("utf-8")
    ).hexdigest()
    assert response.metadata["attempt"] == 1
    assert isinstance(response.metadata["latency_ms"], int)
    assert response.metadata["latency_ms"] >= 0
    assert deepseek_transport.post_calls[0]["headers"]["Authorization"] == (
        f"Bearer {secret}"
    )


def test_provider_requests_do_not_send_secrets_to_google_or_log_source_body() -> None:
    deepseek_transport = FakeTransport(
        post_response=FakeResponse(429, {"error": "rate limited"})
    )
    google_transport = FakeTransport(get_response=FakeResponse(200, google_payload()))
    router = make_router(
        deepseek_transport=deepseek_transport,
        google_transport=google_transport,
    )

    response = router.translate(make_request())

    assert response.translated_text == GOOGLE_TEXT
    assert "deepseek-secret" not in repr(google_transport.get_calls)
    assert SOURCE_TEXT not in repr(response.telemetry)
    assert deepseek_transport.post_calls[0]["headers"]["Authorization"] == (
        "Bearer deepseek-secret"
    )
