from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.sync_knowledge_sources import (
    FULLTEXT_SOURCE_IDS,
    SOURCES,
    parse_llms_index,
    translate_full,
)


class LLMIndexParserTests(unittest.TestCase):
    def test_parse_llms_index_extracts_markdown_links(self):
        text = """
        # Example Docs

        - [Intro](https://example.com/docs/intro.md): Start here
        - [Tools](https://example.com/specification/server/tools.md)
        - [HTML](https://example.com/docs/html-page): ignored because not Markdown
        """

        items = parse_llms_index(
            text,
            public_base_url="https://example.com",
            fetch_base_url="https://example.com",
        )

        self.assertEqual([i["slug"] for i in items], ["docs-intro", "specification-server-tools"])
        self.assertEqual(items[0]["title"], "Intro")
        self.assertEqual(items[0]["url"], "https://example.com/docs/intro")
        self.assertEqual(items[0]["fetchUrl"], "https://example.com/docs/intro.md")
        self.assertEqual(items[0]["categories"], ["Docs"])


class KnowledgeTranslationTests(unittest.TestCase):
    def test_translate_full_never_calls_deepseek_when_env_enabled(self):
        google = MagicMock(return_value="中文正文")
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "sk-test", "TRANSLATE_USE_DEEPSEEK": "1"},
            clear=True,
        ), patch("scripts.sync_knowledge_sources.translate_to_zh_google", google), patch(
            "scripts.sync_knowledge_sources.requests.post"
        ) as post:
            md = translate_full(
                title="Test",
                url="https://example.com/test",
                english="English body",
                source_label="Docs",
                allow_deepseek=True,
            )

        post.assert_not_called()
        google.assert_called()
        self.assertIn("原文：", md)
        self.assertIn("中文正文", md)

    def test_translate_full_preserves_fenced_code_blocks(self):
        english = "Before\n\n```python\nprint('hello')\n```\n\nAfter"

        with patch(
            "scripts.sync_knowledge_sources.translate_to_zh_google",
            side_effect=lambda text: "译：" + text.strip(),
        ):
            md = translate_full(
                title="Code",
                url="https://example.com/code",
                english=english,
                source_label="Docs",
            )

        self.assertIn("```python\nprint('hello')\n```", md)
        self.assertIn("译：Before", md)
        self.assertIn("译：After", md)


class SourceRegistryTests(unittest.TestCase):
    def test_first_batch_sources_are_fulltext_allowlisted(self):
        expected = {
            "mcp_docs",
            "openai_docs",
            "langgraph_docs",
            "llamaindex_docs",
            "anthropic_docs",
        }

        self.assertTrue(expected.issubset(SOURCES.keys()))
        self.assertTrue(expected.issubset(set(FULLTEXT_SOURCE_IDS)))

    def test_publish_step_passes_translation_router_credentials_to_radar(self):
        workflow = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
        match = re.search(
            r"- name: Publish snapshot to way-to-agentic(?P<body>[\s\S]*?)(?:\n      - name:|\Z)",
            workflow,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        for key in (
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_API_BASE_URL",
            "DEEPSEEK_MODEL",
        ):
            self.assertIn(key, body)
        self.assertNotIn("scripts/sync_knowledge_sources.py", body)


if __name__ == "__main__":
    unittest.main()
