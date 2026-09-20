from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


DEFAULT_PROMPT_VERSION = "translation/prompt-v1"
DEFAULT_POLICY_VERSION = "translation/policy-v1"


class ProviderErrorCategory(str, Enum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    INVALID_OUTPUT = "invalid_output"


ProviderErrorType = ProviderErrorCategory


class ProviderError(Exception):
    """A redacted, typed provider failure safe to expose in telemetry."""

    def __init__(
        self,
        provider: str,
        category: ProviderErrorCategory | str,
        reason: str,
        *,
        model: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.provider = str(provider)
        if isinstance(category, str):
            category = ProviderErrorCategory(category.replace("-", "_"))
        self.category = category
        self.reason = str(reason)
        self.model = model
        self.status_code = status_code
        super().__init__(f"{self.provider}:{self.reason}")

    @property
    def error_category(self) -> str:
        return self.category.value

    @property
    def kind(self) -> ProviderErrorCategory:
        return self.category


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_locale: str = "auto"
    target_locale: str = "zh-CN"
    translation_profile: str = "prose/v1"
    prompt_version: str = DEFAULT_PROMPT_VERSION
    policy_version: str = DEFAULT_POLICY_VERSION
    content_id: str | None = None
    revision_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def source_text(self) -> str:
        return self.text

    @property
    def profile(self) -> str:
        return self.translation_profile

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", str(self.text or "").strip())
        object.__setattr__(
            self,
            "source_locale",
            str(self.source_locale or "auto").strip() or "auto",
        )
        object.__setattr__(
            self,
            "target_locale",
            str(self.target_locale or "zh-CN").strip() or "zh-CN",
        )
        object.__setattr__(
            self,
            "translation_profile",
            str(self.translation_profile or "prose/v1").strip() or "prose/v1",
        )
        object.__setattr__(
            self,
            "prompt_version",
            str(self.prompt_version or DEFAULT_PROMPT_VERSION).strip()
            or DEFAULT_PROMPT_VERSION,
        )
        object.__setattr__(
            self,
            "policy_version",
            str(self.policy_version or DEFAULT_POLICY_VERSION).strip()
            or DEFAULT_POLICY_VERSION,
        )


@dataclass(frozen=True)
class ProviderTelemetry:
    provider: str
    model: str | None
    prompt_version: str
    policy_version: str
    input_hash: str
    output_hash: str | None
    latency_ms: int
    attempt: int
    reason: str | None = None
    error_category: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class TranslationResponse:
    translated_text: str | None
    provider: str | None
    model: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    telemetry: tuple[ProviderTelemetry, ...] = ()
    fallback_reason: str | None = None
    reason: str | None = None

    @property
    def text(self) -> str | None:
        return self.translated_text

    @property
    def success(self) -> bool:
        return bool(self.translated_text)

    @property
    def ok(self) -> bool:
        return self.success


class TranslationProvider(ABC):
    name = "provider"

    @property
    def model(self) -> str | None:
        return None

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        raise NotImplementedError


def input_hash(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def output_hash(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


_REFUSAL_PREFIXES = (
    "sorry, i cannot",
    "sorry, i can't",
    "sorry",
    "i cannot",
    "i can't",
    "cannot",
    "i cannot translate",
    "i can't translate",
    "unable",
    "unable to translate",
    "抱歉",
    "很抱歉",
    "无法翻译",
    "无法处理",
    "我无法",
    "我不能",
    "请提供",
    "请直接提供",
)
_LEADING_PUNCTUATION = re.compile(r"^[\s\"'“”‘’「」【】()[\]{}:：,，.!！?？;；-]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def normalize_candidate(
    source: str,
    candidate: Any,
    *,
    target_locale: str = "zh-CN",
) -> str:
    if not isinstance(candidate, str):
        raise ValueError("invalid_output")
    value = candidate.strip()
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:markdown|md)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    value = value.strip("\"'“”‘’「」").strip()
    if not value:
        raise ValueError("invalid_output")
    if value == source.strip():
        raise ValueError("unchanged_output")
    lowered = _LEADING_PUNCTUATION.sub("", value).lower()
    if any(lowered.startswith(prefix) for prefix in _REFUSAL_PREFIXES):
        raise ValueError("refusal")
    if len(source.strip()) >= 8 and len(value) <= 2:
        raise ValueError("invalid_output")
    if (
        target_locale.lower().replace("_", "-").startswith("zh")
        and not _CJK_RE.search(value)
    ):
        raise ValueError("invalid_output")
    return value


def redacted_metadata(
    request: TranslationRequest,
    *,
    provider: str | None,
    model: str | None,
    output: str | None,
    attempt: int,
    latency_ms: int,
    fallback_reason: str | None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "prompt_version": request.prompt_version,
        "policy_version": request.policy_version,
        "translation_profile": request.translation_profile,
        "source_locale": request.source_locale,
        "target_locale": request.target_locale,
        "input_hash": input_hash(request.text),
        "output_hash": output_hash(output),
        "attempt": attempt,
        "latency_ms": int(max(0, latency_ms)),
        "fallback_reason": fallback_reason,
    }
