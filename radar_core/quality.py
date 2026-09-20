from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    issues: tuple[QualityIssue, ...] = ()
    profile: str = ""


_CODE_FENCE_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"(?<!`)`[^`\n]+`(?!`)")
_URL_RE = re.compile(r"https?://[^\s<>\])}]+")
_PLACEHOLDER_RE = re.compile(r"\{\{[^{}\n]+\}\}|<PLACEHOLDER:[^>\n]+>")
_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")


def _matches(pattern: re.Pattern[str], text: str) -> list[str]:
    return pattern.findall(str(text or ""))


def _urls(text: str) -> list[str]:
    values: list[str] = []
    for value in _matches(_URL_RE, text):
        values.append(value.rstrip(".,;:!?，。！？；"))
    return values


def validate_structure(source: str, translated: str) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    source_fences = _matches(_CODE_FENCE_RE, source)
    translated_fences = _matches(_CODE_FENCE_RE, translated)
    if source_fences != translated_fences:
        issues.append(
            QualityIssue(
                "protected_block_changed",
                "code or Mermaid fenced blocks changed during translation",
            )
        )
    if _matches(_INLINE_CODE_RE, source) != _matches(_INLINE_CODE_RE, translated):
        issues.append(
            QualityIssue(
                "inline_code_changed",
                "inline code spans changed during translation",
            )
        )
    if _urls(source) != _urls(translated):
        issues.append(
            QualityIssue("url_changed", "source URLs must be preserved exactly")
        )
    if _matches(_PLACEHOLDER_RE, source) != _matches(_PLACEHOLDER_RE, translated):
        issues.append(
            QualityIssue(
                "placeholder_changed",
                "opaque placeholders must be preserved exactly",
            )
        )
    if len(_matches(_HEADING_RE, source)) != len(_matches(_HEADING_RE, translated)):
        issues.append(
            QualityIssue(
                "heading_structure_changed",
                "heading count changed during translation",
            )
        )
    return issues


def validate_terminology(
    source: str,
    translated: str,
    glossary: Mapping[str, str] | None,
) -> list[QualityIssue]:
    if not glossary:
        return []
    issues: list[QualityIssue] = []
    source_text = str(source or "")
    translated_text = str(translated or "")
    for source_term, target_term in glossary.items():
        if source_term in source_text and target_term not in translated_text:
            issues.append(
                QualityIssue(
                    "glossary_term_missing",
                    f"required terminology missing: {source_term}",
                )
            )
    return issues


def quality_gate(
    source: str,
    translated: str,
    profile: str,
    *,
    glossary: Mapping[str, str] | None = None,
    extra_issues: Iterable[QualityIssue] = (),
) -> QualityReport:
    issues = list(validate_structure(source, translated))
    issues.extend(validate_terminology(source, translated, glossary))
    issues.extend(extra_issues)
    if not str(translated or "").strip():
        issues.append(QualityIssue("empty_output", "translated output is empty"))
    return QualityReport(
        passed=not issues,
        issues=tuple(issues),
        profile=str(profile or ""),
    )
