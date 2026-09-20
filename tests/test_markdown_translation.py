from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from radar_core.translation.base import (
    ProviderTelemetry,
    TranslationRequest,
    TranslationResponse,
)
from radar_core.translation.markdown import (
    MarkdownUnit,
    split_markdown,
    translate_markdown,
)


SOURCE = """# Hello

Read `agent.run()` and visit https://example.test/docs.

```python
print("keep")
```

```mermaid
graph TD
  A --> B
```
"""


@dataclass
class FakeRouter:
    calls: list[str]

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.calls.append(request.text)
        translated = request.text.replace("Hello", "你好").replace("Read", "阅读")
        return TranslationResponse(
            translated_text=translated,
            provider="deepseek",
            model="fake",
            telemetry=(
                ProviderTelemetry(
                    provider="deepseek",
                    model="fake",
                    prompt_version=request.prompt_version,
                    policy_version=request.policy_version,
                    input_hash="input",
                    output_hash="output",
                    latency_ms=0,
                    attempt=1,
                ),
            ),
        )


def test_split_markdown_marks_protected_blocks_and_translatable_prose():
    units = split_markdown(SOURCE)

    assert all(isinstance(unit, MarkdownUnit) for unit in units)
    assert any(unit.kind == "code_fence" for unit in units)
    assert any(unit.kind == "mermaid" for unit in units)
    assert any(unit.translatable and "Hello" in unit.text for unit in units)
    assert any(unit.kind == "inline_code" for unit in units)


def test_translate_markdown_preserves_code_urls_and_inline_code():
    router = FakeRouter(calls=[])
    request = TranslationRequest(
        text=SOURCE,
        source_locale="en",
        target_locale="zh-CN",
        translation_profile="technical/v1",
    )

    result = translate_markdown(SOURCE, request, router)

    assert "你好" in result.translated_text
    assert "阅读" in result.translated_text
    assert "https://example.test/docs" in result.translated_text
    assert "`agent.run()`" in result.translated_text
    assert 'print("keep")' in result.translated_text
    assert "graph TD" in result.translated_text
    assert len(router.calls) >= 1
    assert result.quality.issues == ()


def test_translate_markdown_keeps_repeated_segments_independent_and_reports_failures():
    class EmptyRouter:
        def translate(self, request: TranslationRequest) -> TranslationResponse:
            return TranslationResponse(
                translated_text=None,
                provider=None,
                model=None,
                reason="provider_failed",
            )

    request = TranslationRequest(text="repeat\n\nrepeat", target_locale="zh-CN")
    result = translate_markdown("repeat\n\nrepeat", request, EmptyRouter())

    assert result.translated_text == "repeat\n\nrepeat"
    assert result.quality.passed is False
    assert any(issue.code == "translation_missing" for issue in result.quality.issues)
