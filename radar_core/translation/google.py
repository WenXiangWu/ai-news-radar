from __future__ import annotations

import os
from typing import Any

import requests

from .base import (
    ProviderError,
    ProviderErrorCategory,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
    normalize_candidate,
)


class GoogleProvider(TranslationProvider):
    name = "google"
    DEFAULT_MODEL = "google-translate-gtx"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        transport: Any = None,
        timeout_seconds: float = 20,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.endpoint = (
            str(
                endpoint
                if endpoint is not None
                else os.environ.get(
                    "GOOGLE_TRANSLATE_ENDPOINT",
                    "https://translate.googleapis.com/translate_a/single",
                )
            ).strip()
            or "https://translate.googleapis.com/translate_a/single"
        )
        self.transport = transport if transport is not None else requests.Session()
        self.timeout_seconds = float(timeout_seconds)
        self._model = str(model).strip() or self.DEFAULT_MODEL

    @property
    def model(self) -> str:
        return self._model

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        if not request.text:
            raise ProviderError(
                self.name,
                ProviderErrorCategory.PERMANENT,
                "empty_input",
                model=self.model,
            )

        try:
            response = self.transport.get(
                self.endpoint,
                params={
                    "client": "gtx",
                    "sl": request.source_locale,
                    "tl": request.target_locale,
                    "dt": "t",
                    "q": request.text,
                },
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._transport_error(exc) from None

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code < 200 or status_code >= 300:
            raise self._http_error(status_code)

        try:
            payload = response.json()
        except Exception:
            raise ProviderError(
                self.name,
                ProviderErrorCategory.INVALID_OUTPUT,
                "malformed_json",
                model=self.model,
                status_code=status_code,
            ) from None

        try:
            segments = payload[0]
            candidate = "".join(
                segment[0]
                for segment in segments
                if isinstance(segment, list) and segment and segment[0]
            )
            candidate = normalize_candidate(
                request.text,
                candidate,
                target_locale=request.target_locale,
            )
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "invalid_output"
            if reason not in {
                "refusal",
                "unchanged_output",
                "invalid_output",
            }:
                reason = "invalid_output"
            raise ProviderError(
                self.name,
                ProviderErrorCategory.INVALID_OUTPUT,
                reason,
                model=self.model,
                status_code=status_code,
            ) from None

        return TranslationResponse(
            translated_text=candidate,
            provider=self.name,
            model=self.model,
        )

    def _http_error(self, status_code: int) -> ProviderError:
        if status_code in {408, 429} or status_code >= 500:
            category = ProviderErrorCategory.RETRYABLE
        else:
            category = ProviderErrorCategory.PERMANENT
        reason = f"http_{status_code}" if status_code else "http_error"
        return ProviderError(
            self.name,
            category,
            reason,
            model=self.model,
            status_code=status_code or None,
        )

    def _transport_error(self, exc: Exception) -> ProviderError:
        class_name = type(exc).__name__.lower()
        message = str(exc).lower()
        if isinstance(exc, (TimeoutError, requests.Timeout)) or "timeout" in class_name or "timeout" in message:
            reason = "timeout"
        elif isinstance(exc, (OSError, requests.ConnectionError)):
            reason = "network_error"
        else:
            reason = "transport_error"
        return ProviderError(
            self.name,
            ProviderErrorCategory.RETRYABLE,
            reason,
            model=self.model,
        )
