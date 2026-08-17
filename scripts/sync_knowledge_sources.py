#!/usr/bin/env python3
"""Incremental full-text ZH sync into way-to-agentic frontend/sources/.

Writes the existing contract so the learning site needs no code changes:

  frontend/sources/{vendor}/{kind}/articles.json
  frontend/sources/{vendor}/{kind}/articles/{slug}.md

Only new slugs (or pending without a markdown file) are fetched and translated.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_MON = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}

COOKBOOK_CATS = [
    "Claude Managed Agents",
    "Claude Agent SDK",
    "Agent Patterns",
    "RAG & Retrieval",
    "Integrations",
    "Multimodal",
    "Observability",
    "Cybersecurity",
    "Fine-Tuning",
    "Responses",
    "Thinking",
    "Skills",
    "Evals",
    "Tools",
]

LC_FEATURED = ("Agent Architecture", "Observability & Evals", "Case Studies")
LC_CATS = [
    "Agent Architecture",
    "Observability & Evals",
    "Case Studies",
    "Deep Agents",
    "LangSmith",
    "Open Source",
    "Partner",
    "Conceptual Guide",
    "LangChain Labs",
    "LangGraph",
    "LangChain",
    "Newsletter",
    "Engineering",
    "Deployment",
    "Tutorials & How-Tos",
    "Company Announcements",
    "Harrison's In the Loop",
    "Max Agency Podcast",
    "Systems at LangChain",
]


def today_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def http_get(url: str, timeout: float = 60.0) -> str:
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def html_to_md(html: str, limit: int = 120000) -> str:
    t2 = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    t2 = re.sub(r"<style[\s\S]*?</style>", " ", t2, flags=re.I)
    m = re.search(r"<article[\s\S]*?</article>", t2, re.I)
    chunk = m.group(0) if m else t2
    m2 = re.search(
        r"<(?:main|div)[^>]*(?:prose|markdown|content)[^>]*>[\s\S]*?</(?:main|div)>",
        t2,
        re.I,
    )
    if m2 and (not m or len(m2.group(0)) > len(chunk) * 0.5):
        chunk = m2.group(0)
    chunk = re.sub(
        r"<pre[^>]*><code[^>]*>([\s\S]*?)</code></pre>",
        lambda mm: "\n```\n"
        + html_lib.unescape(re.sub(r"<[^>]+>", "", mm.group(1)))
        + "\n```\n",
        chunk,
        flags=re.I,
    )
    chunk = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.I)
    chunk = re.sub(r"</(p|div|h[1-6]|li|tr)>", "\n", chunk, flags=re.I)
    chunk = re.sub(
        r"<h([1-6])[^>]*>",
        lambda mm: "\n" + "#" * int(mm.group(1)) + " ",
        chunk,
        flags=re.I,
    )
    chunk = re.sub(r"<li[^>]*>", "- ", chunk, flags=re.I)
    chunk = re.sub(r"<[^>]+>", "", chunk)
    chunk = html_lib.unescape(chunk)
    chunk = re.sub(r"\n{3,}", "\n\n", chunk)
    chunk = re.sub(r"[ \t]+\n", "\n", chunk).strip()
    return chunk[:limit]


def parse_eng_date(s: str) -> str:
    m = re.search(
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})",
        s,
    )
    if not m:
        return ""
    return f"{m.group(3)}-{_MON[m.group(1)]}-{int(m.group(2)):02d}"


def parse_month_year(s: str) -> str:
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", s)
    if not m:
        return ""
    return f"{m.group(2)}-{_MON[m.group(1)]}-01"


def load_articles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def save_articles(path: Path, articles: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    articles = sorted(articles, key=lambda a: str(a.get("publishedAt") or ""), reverse=True)
    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_title_zh(md: str, fallback: str) -> str:
    for line in md.splitlines():
        m = re.match(r"^#{1,3}\s+(.+)$", line.strip())
        if m:
            t = m.group(1).strip()
            if t and not t.startswith("原文"):
                return t[:120]
    return fallback


def translate_use_deepseek() -> bool:
    raw = str(os.environ.get("TRANSLATE_USE_DEEPSEEK") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def translate_to_zh_google(text: str) -> str | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": s},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, list) or not payload:
            return None
        segs = payload[0]
        if not isinstance(segs, list):
            return None
        translated = "".join(str(seg[0]) for seg in segs if isinstance(seg, list) and seg and seg[0])
        translated = translated.strip()
        if translated and translated != s:
            return translated
    except Exception:
        return None
    return None


def deepseek_chat(messages: list[dict[str, str]], timeout: int = 300) -> str:
    api_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    base = str(os.environ.get("DEEPSEEK_API_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    model = str(os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "temperature": 0.2, "messages": messages},
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    content = str(((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:markdown|md)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
    return content


def split_chunks(text: str, size: int = 12000) -> list[str]:
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    n = 0
    for para in text.split("\n\n"):
        if n + len(para) + 2 > size and buf:
            parts.append("\n\n".join(buf))
            buf = [para]
            n = len(para)
        else:
            buf.append(para)
            n += len(para) + 2
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def translate_full(
    *,
    title: str,
    url: str,
    english: str,
    source_label: str,
    allow_deepseek: bool = False,
) -> str:
    body = english[:90000]
    header = (
        f"> 原文：[{title}]({url})\n"
        f"> 译文说明：自动翻译校对（对照 {source_label} 原文）\n\n"
    )
    if allow_deepseek and translate_use_deepseek():
        chunks = split_chunks(body, 12000)
        system = (
            f"你是技术文档译者。将 {source_label} 英文文章译为忠实中文 Markdown。"
            "要求：全文翻译非摘要；保留标题层级、列表与代码块（代码与标识符不要翻译）；"
            "术语首次可中英并列；不要臆造原文没有的内容；只输出 Markdown 正文（不要包裹在代码块中）。"
        )
        out: list[str] = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                user = (
                    f"文章标题：{title}\n原文链接：{url}\n\n"
                    "请在文首固定两行 blockquote：\n"
                    f"{header}"
                    f"英文正文（第 {i + 1}/{len(chunks)} 段）：\n{chunk}"
                )
            else:
                user = (
                    f"续译同一篇文章《{title}》，不要重复文首说明，不要重复已译内容。"
                    f"这是第 {i + 1}/{len(chunks)} 段：\n{chunk}"
                )
            out.append(deepseek_chat([{"role": "system", "content": system}, {"role": "user", "content": user}]))
            time.sleep(0.4)
        md = "\n\n".join(out).strip()
    else:
        chunks = split_chunks(body, 3500)
        parts: list[str] = []
        for chunk in chunks:
            zh = translate_to_zh_google(chunk)
            if not zh:
                raise RuntimeError("Google 翻译失败")
            parts.append(zh)
            time.sleep(0.2)
        md = "\n\n".join(parts).strip()
    if "原文：" not in md[:400]:
        md = header + md
    return md


# --- source parsers ---


def list_engineering(_root: Path) -> list[dict[str, Any]]:
    html = http_get("https://www.anthropic.com/engineering")
    parts = re.split(r'href="(/engineering/[^"#?]+)"', html)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for i in range(1, len(parts), 2):
        href = parts[i]
        slug = href.rsplit("/", 1)[-1]
        if not slug or slug in seen or slug == "engineering":
            continue
        seen.add(slug)
        after = parts[i + 1][:1600] if i + 1 < len(parts) else ""
        before = parts[i - 1][-500:] if i > 0 else ""
        titles = re.findall(r">([^<]{8,160})<", after)
        title = next(
            (
                t.strip()
                for t in titles
                if t.strip() and "Skip" not in t and "Featured" not in t and "Start building" not in t
            ),
            slug,
        )
        items.append(
            {
                "slug": slug,
                "title": html_lib.unescape(title),
                "publishedAt": parse_eng_date(before + after) or today_shanghai(),
                "url": "https://www.anthropic.com/engineering/" + slug,
            }
        )
    return items


def fetch_engineering(item: dict[str, Any]) -> str:
    return html_to_md(http_get(item["url"]))


def list_claude_cookbook(_root: Path) -> list[dict[str, Any]]:
    html = http_get("https://platform.claude.com/cookbook/")
    parts = re.split(r'href="(/cookbook/[a-z0-9][a-z0-9\-]{2,})"', html)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    slug_ok = re.compile(r"^[a-z0-9][a-z0-9\-]{2,160}$")
    for i in range(1, len(parts), 2):
        href = parts[i]
        slug = href.rsplit("/", 1)[-1]
        if not slug_ok.match(slug) or slug in seen or "." in slug:
            continue
        seen.add(slug)
        after = parts[i + 1][:2400] if i + 1 < len(parts) else ""
        before = parts[i - 1][-800:] if i > 0 else ""
        titles = re.findall(r">([^<]{6,200})<", after)
        title = next(
            (
                t.strip()
                for t in titles
                if t.strip() and "Skip" not in t and "Category" not in t and t.strip() not in COOKBOOK_CATS
            ),
            slug,
        )
        cats: list[str] = []
        for m in re.finditer(r"[?&]category=([^\"'&\s]+)", after):
            name = html_lib.unescape(unquote(m.group(1).replace("+", " ")))
            if name and name not in cats:
                cats.append(name)
        items.append(
            {
                "slug": slug,
                "title": html_lib.unescape(title),
                "publishedAt": parse_month_year(after) or parse_month_year(before[-240:]) or (today_shanghai()[:7] + "-01"),
                "url": "https://platform.claude.com/cookbook/" + slug,
                "categories": cats,
            }
        )
    return items


def fetch_claude_cookbook(item: dict[str, Any]) -> str:
    text = html_to_md(http_get(item["url"]))
    return re.sub(r"^Claude Cookbook\s*", "", text)


def list_openai_cookbook(_root: Path) -> list[dict[str, Any]]:
    html = http_get("https://developers.openai.com/cookbook")
    parts = re.split(r'href="(/cookbook/(?:examples|articles)/[^"#?]+)"', html)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for i in range(1, len(parts), 2):
        href = html_lib.unescape(parts[i]).rstrip("/")
        if href in seen:
            continue
        seen.add(href)
        after = parts[i + 1][:3200] if i + 1 < len(parts) else ""
        slug = href.removeprefix("/cookbook/").replace("/", "-")
        titles = re.findall(r">([^<]{6,200})<", after)
        title = next((t.strip() for t in titles if t.strip() and "Cookbook" not in t), slug)
        items.append(
            {
                "slug": slug,
                "title": html_lib.unescape(title),
                "publishedAt": parse_eng_date(after) or today_shanghai(),
                "url": urljoin("https://developers.openai.com/", href.lstrip("/")),
            }
        )
    return items


def fetch_openai_cookbook(item: dict[str, Any]) -> str:
    return html_to_md(http_get(item["url"]))


def _lc_date(s: str) -> str:
    m = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2}),\s+(\d{4})",
        s,
    )
    if not m:
        return ""
    months = {
        "January": "01",
        "February": "02",
        "March": "03",
        "April": "04",
        "May": "05",
        "June": "06",
        "July": "07",
        "August": "08",
        "September": "09",
        "October": "10",
        "November": "11",
        "December": "12",
    }
    return f"{m.group(3)}-{months[m.group(1)]}-{int(m.group(2)):02d}"


def _lc_cats(blob: str) -> list[str]:
    found: list[str] = []
    for cat in LC_CATS:
        if cat in blob and cat not in found:
            found.append(cat)
    return found


def list_langchain(_root: Path) -> list[dict[str, Any]]:
    featured: dict[str, dict[str, Any]] = {}
    for page in range(1, 4):
        html = http_get(f"https://www.langchain.com/blog?8457a1db_page={page}")
        parts = re.split(r'<div[^>]*class="blog-item[^"]*"', html)
        for part in parts[1:]:
            href = re.search(r'href="(/blog/[a-z0-9\-]+)"', part)
            if not href:
                continue
            slug = href.group(1).rsplit("/", 1)[-1]
            title_m = re.search(r"<h[23][^>]*>(.*?)</h[23]>", part, re.S)
            title = html_lib.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip() if title_m else slug
            cats = _lc_cats(part[:2500])
            tabs = [c for c in cats if c in LC_FEATURED]
            if not tabs:
                continue
            if slug not in featured:
                featured[slug] = {
                    "slug": slug,
                    "title": title,
                    "publishedAt": _lc_date(part[:4000]),
                    "url": f"https://www.langchain.com/blog/{slug}",
                    "categories": cats,
                    "featuredTabs": tabs,
                }
        time.sleep(0.12)
    return list(featured.values())


def slug_from_url(url: str, title: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    slug = re.sub(r"[^a-zA-Z0-9\-]+", "-", slug).strip("-").lower()
    if len(slug) < 3:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:80].strip("-")
    return slug or "item"


def _entry_date(entry: Any) -> str:
    for key in ("published", "updated", "pubDate"):
        raw = str(entry.get(key) or "")
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        m = re.search(
            r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})",
            raw,
        )
        if m:
            return f"{m.group(3)}-{_MON[m.group(1)]}-{int(m.group(2)):02d}"
    return ""


def list_rss(feed_url: str, *, keywords: list[str] | None = None, limit: int = 24) -> list[dict[str, Any]]:
    parsed = feedparser.parse(feed_url)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in list(parsed.entries)[:limit]:
        title = str(entry.get("title") or "").strip()
        link = str(entry.get("link") or "").strip()
        if not title or not link:
            continue
        if keywords:
            hay = f"{title} {link}".lower()
            if not any(k.lower() in hay for k in keywords):
                continue
        slug = slug_from_url(link, title)
        if slug in seen:
            continue
        seen.add(slug)
        items.append(
            {
                "slug": slug,
                "title": html_lib.unescape(title),
                "publishedAt": _entry_date(entry) or today_shanghai(),
                "url": link,
            }
        )
    return items


def fetch_article_url(item: dict[str, Any]) -> str:
    return html_to_md(http_get(item["url"]))


def make_rss_lister(feed_url: str, keywords: list[str] | None = None):
    def _list(_root: Path) -> list[dict[str, Any]]:
        return list_rss(feed_url, keywords=keywords)

    return _list


def list_anthropic_news(_root: Path) -> list[dict[str, Any]]:
    html = http_get("https://www.anthropic.com/news")
    parts = re.split(r'href="(/news/[^"#?]+)"', html)
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for i in range(1, len(parts), 2):
        href = parts[i]
        slug = href.rsplit("/", 1)[-1]
        if not slug or slug in seen or slug == "news":
            continue
        seen.add(slug)
        after = parts[i + 1][:1600] if i + 1 < len(parts) else ""
        titles = re.findall(r">([^<]{8,180})<", after)
        title = next((t.strip() for t in titles if t.strip() and "News" not in t[:12]), slug)
        items.append(
            {
                "slug": slug,
                "title": html_lib.unescape(title),
                "publishedAt": parse_eng_date(after) or today_shanghai(),
                "url": urljoin("https://www.anthropic.com", href),
            }
        )
    return items


def fetch_langchain(item: dict[str, Any]) -> str:
    html = http_get(item["url"])
    t2 = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    t2 = re.sub(r"<style[\s\S]*?</style>", " ", t2, flags=re.I)
    idx = t2.find("blog-post-content")
    chunk = ""
    if idx >= 0:
        region = t2[idx : idx + 250000]
        blocks = re.findall(
            r'<div[^>]*class="[^"]*w-richtext[^"]*"[^>]*>([\s\S]*?)</div>',
            region,
            re.I,
        )
        chunk = "\n\n".join(blocks) if blocks else region
    if not chunk:
        chunk = t2
    text = html_to_md(chunk)
    title = str(item.get("title") or "")
    if title and not text.lstrip().startswith("#"):
        text = f"# {title}\n\n{text}"
    return text


SourceFn = Callable[[Path], list[dict[str, Any]]]
FetchFn = Callable[[dict[str, Any]], str]

SOURCES: dict[str, dict[str, Any]] = {
    "engineering": {
        "rel": "anthropic/engineering",
        "label": "Anthropic Engineering",
        "list": list_engineering,
        "fetch": fetch_engineering,
    },
    "cookbook": {
        "rel": "anthropic/cookbook",
        "label": "Claude Cookbook",
        "list": list_claude_cookbook,
        "fetch": fetch_claude_cookbook,
    },
    "openai_cookbook": {
        "rel": "openai/cookbook",
        "label": "OpenAI Cookbook",
        "list": list_openai_cookbook,
        "fetch": fetch_openai_cookbook,
    },
    "langchain_blog": {
        "rel": "langchain/blog",
        "label": "LangChain Blog",
        "list": list_langchain,
        "fetch": fetch_langchain,
    },
    "anthropic_news": {
        "rel": "anthropic/news",
        "label": "Anthropic News",
        "list": list_anthropic_news,
        "fetch": fetch_article_url,
    },
    "openai_news": {
        "rel": "openai/news",
        "label": "OpenAI News",
        "list": make_rss_lister("https://openai.com/news/rss.xml"),
        "fetch": fetch_article_url,
    },
    "huggingface_blog": {
        "rel": "huggingface/blog",
        "label": "Hugging Face Blog",
        "list": make_rss_lister("https://huggingface.co/blog/feed.xml"),
        "fetch": fetch_article_url,
    },
    "deepmind_blog": {
        "rel": "deepmind/blog",
        "label": "Google DeepMind Blog",
        "list": make_rss_lister("https://deepmind.google/blog/rss.xml"),
        "fetch": fetch_article_url,
    },
    "google_research": {
        "rel": "google/research",
        "label": "Google Research Blog",
        "list": make_rss_lister(
            "https://research.google/blog/rss/",
            ["ai", "agent", "gemini", "llm", "rag", "language model", "reasoning"],
        ),
        "fetch": fetch_article_url,
    },
    "azure_foundry": {
        "rel": "microsoft/foundry",
        "label": "Azure AI Foundry Blog",
        "list": make_rss_lister(
            "https://devblogs.microsoft.com/foundry/feed/",
            ["agent", "semantic kernel", "autogen", "foundry", "copilot"],
        ),
        "fetch": fetch_article_url,
    },
    "nvidia_devblog": {
        "rel": "nvidia/blog",
        "label": "NVIDIA Developer Blog",
        "list": make_rss_lister(
            "https://developer.nvidia.com/blog/feed",
            ["agent", "nemo", "nim", "rag", "llm", "inference", "cosmos"],
        ),
        "fetch": fetch_article_url,
    },
    "qwen_blog": {
        "rel": "qwen/blog",
        "label": "Qwen Blog",
        "list": make_rss_lister("https://qwenlm.github.io/blog/index.xml"),
        "fetch": fetch_article_url,
    },
    "simon_willison": {
        "rel": "simonwillison/blog",
        "label": "Simon Willison",
        "list": make_rss_lister(
            "https://simonwillison.net/atom/everything/",
            ["llm", "agent", "mcp", "tool", "eval", "prompt", "claude", "gpt"],
        ),
        "fetch": fetch_article_url,
    },
    "hamel_dev": {
        "rel": "hamel/blog",
        "label": "Hamel Husain",
        "list": make_rss_lister("https://hamel.dev/index.xml"),
        "fetch": fetch_article_url,
    },
    "lilian_weng": {
        "rel": "lilianweng/blog",
        "label": "Lilian Weng",
        "list": make_rss_lister("https://lilianweng.github.io/index.xml"),
        "fetch": fetch_article_url,
    },
    "chip_huyen": {
        "rel": "huyenchip/blog",
        "label": "Chip Huyen",
        "list": make_rss_lister("https://huyenchip.com/feed.xml"),
        "fetch": fetch_article_url,
    },
    "eugene_yan": {
        "rel": "eugeneyan/blog",
        "label": "Eugene Yan",
        "list": make_rss_lister("https://eugeneyan.com/rss/"),
        "fetch": fetch_article_url,
    },
    "jxnl": {
        "rel": "jxnl/blog",
        "label": "Jason Liu",
        "list": make_rss_lister("https://jxnl.co/feed_rss_created.xml"),
        "fetch": fetch_article_url,
    },
    "interconnects": {
        "rel": "interconnects/blog",
        "label": "Interconnects",
        "list": make_rss_lister("https://www.interconnects.ai/feed"),
        "fetch": fetch_article_url,
    },
    "latent_space": {
        "rel": "latentspace/blog",
        "label": "Latent Space",
        "list": make_rss_lister("https://www.latent.space/feed"),
        "fetch": fetch_article_url,
    },
}

# 仅站内已有阅读器的 4 个知识源做全文翻译；新增源先在学习站加栏目再写入此集合。
FULLTEXT_SOURCE_IDS: tuple[str, ...] = (
    "engineering",
    "cookbook",
    "openai_cookbook",
    "langchain_blog",
)


def sync_one(source_id: str, sources_root: Path, max_new: int) -> dict[str, Any]:
    if source_id not in FULLTEXT_SOURCE_IDS:
        return {
            "ok": True,
            "source": source_id,
            "skipped": "fulltext not allowlisted",
            "translated": [],
            "errors": [],
        }
    cfg = SOURCES[source_id]
    dest = sources_root / cfg["rel"]
    json_path = dest / "articles.json"
    md_dir = dest / "articles"
    raw_dir = dest / "raw"
    md_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    remote = cfg["list"](sources_root)
    local = load_articles(json_path)
    by_slug = {str(a.get("slug")): a for a in local if a.get("slug")}
    new_slugs: list[str] = []
    for it in remote:
        slug = it["slug"]
        if slug not in by_slug:
            new_slugs.append(slug)
            row = {
                "slug": slug,
                "title": it["title"],
                "titleZh": it["title"],
                "publishedAt": it.get("publishedAt") or today_shanghai(),
                "url": it["url"],
                "status": "pending",
            }
            if it.get("categories") is not None:
                row["categories"] = it["categories"]
            if it.get("featuredTabs") is not None:
                row["featuredTabs"] = it["featuredTabs"]
            by_slug[slug] = row
        else:
            cur = by_slug[slug]
            cur["title"] = it["title"] or cur.get("title")
            if it.get("publishedAt"):
                cur["publishedAt"] = it["publishedAt"]
            cur["url"] = it["url"]
            if it.get("categories") is not None:
                cur["categories"] = it["categories"]
            if it.get("featuredTabs") is not None:
                cur["featuredTabs"] = it["featuredTabs"]

    pending = [
        s
        for s in new_slugs
        if by_slug[s].get("status") != "translated" or not (md_dir / f"{s}.md").exists()
    ]
    # also retry old pending with no md
    for slug, meta in by_slug.items():
        if slug in pending:
            continue
        if meta.get("status") != "translated" and not (md_dir / f"{slug}.md").exists() and meta.get("url"):
            pending.append(slug)

    translated: list[str] = []
    errors: list[str] = []
    for slug in pending[: max(0, max_new)]:
        meta = by_slug[slug]
        try:
            text = cfg["fetch"](meta)
            (raw_dir / f"{slug}.txt").write_text(text, encoding="utf-8")
            if len(text) < 200:
                raise RuntimeError("正文过短，可能抓取失败")
            md = translate_full(
                title=str(meta.get("title") or slug),
                url=str(meta["url"]),
                english=text,
                source_label=str(cfg["label"]),
                allow_deepseek=source_id in FULLTEXT_SOURCE_IDS,
            )
            if len(md) < 200:
                raise RuntimeError("译文过短")
            (md_dir / f"{slug}.md").write_text(md, encoding="utf-8")
            meta["status"] = "translated"
            meta["titleZh"] = extract_title_zh(md, str(meta.get("title") or slug))
            translated.append(slug)
            print(f"[{source_id}] translated {slug}", flush=True)
        except Exception as exc:  # noqa: BLE001
            meta["status"] = "pending"
            errors.append(f"{slug}: {exc}")
            print(f"[{source_id}] fail {slug}: {exc}", flush=True)

    for slug, meta in by_slug.items():
        if (md_dir / f"{slug}.md").exists() and meta.get("status") != "translated":
            meta["status"] = "translated"

    save_articles(json_path, list(by_slug.values()))
    return {
        "ok": not (pending and not translated and errors),
        "source": source_id,
        "remote": len(remote),
        "local": len(by_slug),
        "new": len(new_slugs),
        "translated": translated,
        "errors": errors,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Full-text ZH sync into frontend/sources/")
    p.add_argument(
        "--sources-root",
        required=True,
        help="Path to way-to-agentic frontend/sources",
    )
    p.add_argument(
        "--only",
        default=",".join(FULLTEXT_SOURCE_IDS),
        help="Comma-separated source ids, or 'all' (only the 4 site knowledge sources)",
    )
    p.add_argument("--max-new", type=int, default=2, help="Max new articles to translate per source")
    p.add_argument(
        "--max-new-total",
        type=int,
        default=4,
        help="Max new articles to translate across all sources in one run",
    )
    p.add_argument(
        "--report-out",
        default="",
        help="Write a JSON report for the learning-site scheduler",
    )
    args = p.parse_args()
    root = Path(args.sources_root).resolve()
    if not root.exists():
        print(f"sources root missing: {root}", file=sys.stderr)
        return 1
    if args.only.strip() == "all":
        wanted = list(FULLTEXT_SOURCE_IDS)
    else:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
    unknown = [x for x in wanted if x not in SOURCES]
    if unknown:
        print(f"unknown sources: {unknown}", file=sys.stderr)
        return 1
    blocked = [x for x in wanted if x not in FULLTEXT_SOURCE_IDS]
    if blocked:
        print(f"skip non-fulltext sources: {blocked}", flush=True)
        wanted = [x for x in wanted if x in FULLTEXT_SOURCE_IDS]
    remaining = max(0, args.max_new_total)
    failed = 0
    results: list[dict] = []
    for sid in wanted:
        budget = min(args.max_new, remaining)
        try:
            result = sync_one(sid, root, budget)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "source": sid, "errors": [str(exc)]}
        remaining -= len(result.get("translated") or [])
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result.get("ok"):
            failed += 1
    if args.report_out:
        Path(args.report_out).write_text(
            json.dumps(
                {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "ok": failed == 0,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
