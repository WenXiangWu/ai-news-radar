from __future__ import annotations

from radar_core.quality import (
    quality_gate,
    validate_structure,
    validate_terminology,
)


SOURCE = """# Title

Open `run()` at https://example.test/run.

```python
print("ok")
```
"""


def test_quality_gate_accepts_structurally_safe_translation():
    translated = """# 标题

打开 `run()` 查看 https://example.test/run。

```python
print("ok")
```
"""

    report = quality_gate(SOURCE, translated, "technical/v1")

    assert report.passed is True
    assert report.issues == ()


def test_quality_gate_rejects_changed_urls_and_code_fences():
    translated = """# 标题

打开 `run()` 查看 https://evil.test/run。

```python
print("changed")
```
"""

    issues = validate_structure(SOURCE, translated)

    assert {issue.code for issue in issues} >= {"url_changed", "protected_block_changed"}


def test_terminology_reports_missing_glossary_term():
    issues = validate_terminology(
        "Use the agent runtime.",
        "使用运行时。",
        {"agent runtime": "智能体运行时"},
    )

    assert issues[0].code == "glossary_term_missing"
