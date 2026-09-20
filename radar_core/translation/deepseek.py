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


class DeepSeekProvider(TranslationProvider):
    name = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        transport: Any = None,
        timeout_seconds: float = 20,
    ) -> None:
        self._api_key = (
            str(api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", ""))
            .strip()
        )
        self.base_url = (
            str(
                base_url
                if base_url is not None
                else os.environ.get("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com")
            )
            .strip()
            .rstrip("/")
            or "https://api.deepseek.com"
        )
        self._model = (
            str(
                model
                if model is not None
                else os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
            ).strip()
            or "deepseek-chat"
        )
        self.transport = transport if transport is not None else requests.Session()
        self.timeout_seconds = float(timeout_seconds)

    @property
    def model(self) -> str:
        return self._model

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(model={self.model!r}, "
            f"configured={bool(self._api_key)})"
        )

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        if not self._api_key:
            raise ProviderError(
                self.name,
                ProviderErrorCategory.PERMANENT,
                "missing_api_key",
                model=self.model,
            )
        if not request.text:
            raise ProviderError(
                self.name,
                ProviderErrorCategory.PERMANENT,
                "empty_input",
                model=self.model,
            )

        try:
            response = self.transport.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "messages": [
                        {
                            "role": "system",
                            "content": self._system_prompt(request),
                        },
                        {"role": "user", "content": request.text},
                    ],
                },
                timeout=self.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._transport_error(exc) from None

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code != 200:
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
            choices = payload["choices"]
            message = choices[0]["message"]
            candidate = normalize_candidate(
                request.text,
                message["content"],
                target_locale=request.target_locale,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
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

    def _system_prompt(self, request: TranslationRequest) -> str:
        return (
            "Translate the source text into the requested target locale. "
            "Preserve product names, company names, model names, URLs, "
            "identifiers, and technical terms. Return only the translation, "
            "without explanations, quotes, or refusal text. "
            f"Target locale: {request.target_locale}. "
            f"Translation profile: {request.translation_profile}. "
            f"Prompt version: {request.prompt_version}."
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
