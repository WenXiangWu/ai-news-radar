from __future__ import annotations

import time
from typing import Any

from .base import (
    ProviderError,
    ProviderErrorCategory,
    ProviderTelemetry,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
    input_hash,
    output_hash,
    redacted_metadata,
)
from .deepseek import DeepSeekProvider
from .google import GoogleProvider


class TranslationRouter:
    """Route translations through DeepSeek first and Google second."""

    def __init__(
        self,
        *,
        deepseek: TranslationProvider | None = None,
        google: TranslationProvider | None = None,
    ) -> None:
        self._providers = (
            deepseek if deepseek is not None else DeepSeekProvider(),
            google if google is not None else GoogleProvider(),
        )

    def provider_order(self) -> tuple[str, ...]:
        return tuple(self._provider_name(provider) for provider in self._providers)

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        if not request.text:
            return TranslationResponse(
                translated_text=None,
                provider=None,
                model=None,
                metadata=redacted_metadata(
                    request,
                    provider=None,
                    model=None,
                    output=None,
                    attempt=0,
                    latency_ms=0,
                    fallback_reason=None,
                ),
                telemetry=(),
                reason="empty_input",
            )

        telemetry: list[ProviderTelemetry] = []
        first_failure: str | None = None
        total_latency_ms = 0

        for attempt, provider in enumerate(self._providers, start=1):
            provider_name = self._provider_name(provider)
            provider_model = self._provider_model(provider)
            started = time.perf_counter()
            try:
                result = provider.translate(request)
            except ProviderError as error:
                latency_ms = self._elapsed_ms(started)
                total_latency_ms += latency_ms
                failure = f"{provider_name}:{error.reason}"
                if first_failure is None:
                    first_failure = failure
                telemetry.append(
                    ProviderTelemetry(
                        provider=provider_name,
                        model=error.model or provider_model,
                        prompt_version=request.prompt_version,
                        policy_version=request.policy_version,
                        input_hash=input_hash(request.text),
                        output_hash=None,
                        latency_ms=latency_ms,
                        attempt=attempt,
                        reason=error.reason,
                        error_category=error.category.value,
                        fallback_reason=first_failure if attempt > 1 else None,
                    )
                )
                continue
            except Exception:
                latency_ms = self._elapsed_ms(started)
                total_latency_ms += latency_ms
                failure = f"{provider_name}:unexpected_error"
                if first_failure is None:
                    first_failure = failure
                telemetry.append(
                    ProviderTelemetry(
                        provider=provider_name,
                        model=provider_model,
                        prompt_version=request.prompt_version,
                        policy_version=request.policy_version,
                        input_hash=input_hash(request.text),
                        output_hash=None,
                        latency_ms=latency_ms,
                        attempt=attempt,
                        reason="unexpected_error",
                        error_category=ProviderErrorCategory.RETRYABLE.value,
                        fallback_reason=first_failure if attempt > 1 else None,
                    )
                )
                continue

            latency_ms = self._elapsed_ms(started)
            total_latency_ms += latency_ms
            text = result.translated_text
            selected_provider = result.provider or provider_name
            selected_model = result.model or provider_model
            telemetry.append(
                ProviderTelemetry(
                    provider=selected_provider,
                    model=selected_model,
                    prompt_version=request.prompt_version,
                    policy_version=request.policy_version,
                    input_hash=input_hash(request.text),
                    output_hash=output_hash(text),
                    latency_ms=latency_ms,
                    attempt=attempt,
                    fallback_reason=first_failure,
                )
            )
            if text:
                metadata = redacted_metadata(
                    request,
                    provider=selected_provider,
                    model=selected_model,
                    output=text,
                    attempt=attempt,
                    latency_ms=total_latency_ms,
                    fallback_reason=first_failure,
                )
                return TranslationResponse(
                    translated_text=text,
                    provider=selected_provider,
                    model=selected_model,
                    metadata=metadata,
                    telemetry=tuple(telemetry),
                    fallback_reason=first_failure,
                )

            failure = f"{provider_name}:invalid_output"
            if first_failure is None:
                first_failure = failure
            telemetry[-1] = ProviderTelemetry(
                **{
                    **telemetry[-1].__dict__,
                    "reason": "invalid_output",
                    "error_category": ProviderErrorCategory.INVALID_OUTPUT.value,
                    "fallback_reason": first_failure if attempt > 1 else None,
                }
            )

        last = telemetry[-1] if telemetry else None
        final_reason = (
            f"{last.provider}:{last.reason}"
            if last is not None and last.reason
            else first_failure
        )
        metadata = redacted_metadata(
            request,
            provider=None,
            model=None,
            output=None,
            attempt=len(telemetry),
            latency_ms=total_latency_ms,
            fallback_reason=first_failure,
        )
        return TranslationResponse(
            translated_text=None,
            provider=None,
            model=None,
            metadata=metadata,
            telemetry=tuple(telemetry),
            fallback_reason=first_failure,
            reason=final_reason,
        )

    @staticmethod
    def _provider_name(provider: Any) -> str:
        return str(getattr(provider, "name", provider.__class__.__name__.lower()))

    @staticmethod
    def _provider_model(provider: Any) -> str | None:
        model = getattr(provider, "model", None)
        return str(model) if model else None

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))
