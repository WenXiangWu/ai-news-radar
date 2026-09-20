from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from ..quality import QualityIssue, QualityReport, quality_gate
from .base import TranslationRequest, TranslationResponse


@dataclass(frozen=True)
class MarkdownUnit:
    index: int
    kind: str
    text: str
    translatable: bool


@dataclass(frozen=True)
class TranslationArtifact:
    translated_text: str
    quality: QualityReport
    responses: tuple[TranslationResponse, ...] = ()


_FENCE_START_RE = re.compile(r"(?m)^[ \t]*```([^\n]*)\n")
_INLINE_TOKEN_RE = re.compile(r"`[^`\n]+`|https?://[^\s<>\])}]+")


def _inline_units(text: str, start_index: int) -> list[MarkdownUnit]:
    units: list[MarkdownUnit] = []
    cursor = 0
    index = start_index
    for match in _INLINE_TOKEN_RE.finditer(text):
        if match.start() > cursor:
            chunk = text[cursor : match.start()]
            units.append(
                MarkdownUnit(
                    index=index,
                    kind="prose" if chunk.strip() else "whitespace",
                    text=chunk,
                    translatable=bool(chunk.strip()),
                )
            )
            index += 1
        token = match.group(0)
        kind = "inline_code" if token.startswith("`") else "url"
        units.append(
            MarkdownUnit(
                index=index,
                kind=kind,
                text=token,
                translatable=False,
            )
        )
        index += 1
        cursor = match.end()
    if cursor < len(text):
        chunk = text[cursor:]
        units.append(
            MarkdownUnit(
                index=index,
                kind="prose" if chunk.strip() else "whitespace",
                text=chunk,
                translatable=bool(chunk.strip()),
            )
        )
    return units


def split_markdown(document: str) -> list[MarkdownUnit]:
    text = str(document or "")
    units: list[MarkdownUnit] = []
    cursor = 0
    index = 0
    while True:
        match = _FENCE_START_RE.search(text, cursor)
        if match is None:
            units.extend(_inline_units(text[cursor:], index))
            break
        if match.start() > cursor:
            prefix_units = _inline_units(text[cursor : match.start()], index)
            units.extend(prefix_units)
            index += len(prefix_units)
        language = match.group(1).strip().lower()
        closing = text.find("```", match.end())
        if closing == -1:
            closing = len(text) - 3
        end = min(len(text), closing + 3)
        block = text[match.start() : end]
        kind = "mermaid" if language == "mermaid" else "code_fence"
        units.append(
            MarkdownUnit(
                index=index,
                kind=kind,
                text=block,
                translatable=False,
            )
        )
        index += 1
        cursor = end
        if cursor >= len(text):
            break
    return units


def translate_markdown(
    document: str,
    request: TranslationRequest,
    router: Any,
) -> TranslationArtifact:
    units = split_markdown(document)
    translated_parts: list[str] = []
    responses: list[TranslationResponse] = []
    extra_issues: list[QualityIssue] = []
    for unit in units:
        if not unit.translatable:
            translated_parts.append(unit.text)
            continue
        unit_request = replace(request, text=unit.text)
        response = router.translate(unit_request)
        responses.append(response)
        translated = response.translated_text
        if not translated:
            translated_parts.append(unit.text)
            extra_issues.append(
                QualityIssue(
                    "translation_missing",
                    f"no translation returned for Markdown unit {unit.index}",
                )
            )
            continue
        translated_parts.append(translated)
    translated_text = "".join(translated_parts)
    quality = quality_gate(
        document,
        translated_text,
        request.translation_profile,
        extra_issues=extra_issues,
    )
    return TranslationArtifact(
        translated_text=translated_text,
        quality=quality,
        responses=tuple(responses),
    )
