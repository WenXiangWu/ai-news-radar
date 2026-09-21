# 生产级内容中心技术方案

**日期：** 2026-08-19  
**状态：** 待评审（对齐后待写实施计划）  
**范围：** `日报抓取/ai-news-radar`（生产管线）+ `way-to-agentic`（学习站消费面）  
**对照：** 2026-08-17 精选 20 条尸检；[AIHOT](https://aihot.virxact.com/) 的卡片完整度与六档形态

---

## 1. 目标与非目标

### 1.1 要交付什么

读者打开学习站前沿，5 分钟内完成三件事：

1. **今天发生了什么**：按形态看到模型 / 产品 / 行业 / 论文 / 教程 / 观点。
2. **为什么这条在榜上**：可信发布方 + 2–4 句事实摘要 + 一句推荐理由。
3. **同一件事只出现一次**：其它报道收进「亦见于」，不拆成多条。

日 / 周 / 月是同一套稿件的三种时间切片。开源热度、模型评测、官方长文精读是另外三类对象，各自成栏，不混进六区。

### 1.2 非目标（本方案不做）

- 不自建 X 爬虫，不接 Nitter / RSSHub。X 继续读 Follow Builders 公开 `feed-x.json`。
- 不改模型共识分算法，不让今日稿改名次。
- 不把 GitHub Trending / Trendshift 仓库当新闻稿。
- 不把 Engineering / Cookbook 全文再复制一份到雷达 JSON。
- 不在动态筛栏加主题轨 `tracks`（留给学习路径 / 一手库）。
- 不重写 Actions 大结构：仍是每小时 `:17` 一轮，知识源同步仍挂在同一 job。

### 1.3 成功标准（可验收）

| # | 标准 |
|---|---|
| S1 | 今日六区标题恒在；空区文案为「今日无一手更新」，链到 `?v=stream&form=<id>`，不靠关键词猜填。 |
| S2 | 今日公开卡片 8 项齐全：中文标题、摘要、理由、`origin`、规范 URL、`form`、`source_class`、时间。缺一项不进六区。 |
| S3 | `source_class=aggregator` 时 `official` 恒为 `false`；展示源是发布方，不是 buzzing / newsnow。 |
| S4 | 同一规范 URL 或 6 小时内标题相似度 ≥ 0.86 只出一条故事。 |
| S5 | 上海日历日封存后，该日 `dailies/{date}.json` 不再被下一小时改写。 |
| S6 | 周报 / 月报只从已封存日报派生；`weekly/index.json`、`monthly/index.json` 保留全部历史，不丢 latest。 |
| S7 | 动态五维 AND 可组合；时间窗 24h / 7d 独立；缺字段的旧快照禁用对应维，不猜词。 |
| S8 | 开源榜保留官方名次；模型榜分数只来自现有 `leaderboard.json`。 |
| S9 | 一手库只读 `frontend/sources/**/articles.json`；已译点进站内阅读器，未译开原文。 |
| S10 | 单源失败不打断整轮；分类失败的条目不进六区配额。 |

---

## 2. 对象模型（四类互不混）

| 对象 | 身份 | 出现在 | 禁止 |
|---|---|---|---|
| **稿件 Story** | 一件事的合并报道 | 今日六区、周报、月报、动态 | 仓库卡、榜单行、长文正文 |
| **仓库 Repo** | GitHub 项目热度 | 开源榜；今日右侧「今日开源」最多 8 条 | 六区 `items[]` |
| **型号 Model** | 共识评测行 | 模型榜；今日「模型共识」Top 3 | 六区 `items[]` |
| **精读 Reading** | 官方长文译本 | 一手库目录 + 知识源阅读器 | 雷达 JSON 再存一份 md |

稿件可以**引用**其它对象（「看榜单」「站内精读」），但不吸收它们的正文或分数。

---

## 3. 信息架构

侧栏六档：

| id | 栏目 | 数据 |
|---|---|---|
| `today` | 今日 | `dailies/{date}.json` 的 `sections` + `opensource` 摘要 + 模型 Top 3 |
| `week` / `month` | 周报 / 月报 | `weekly/{id}.json` / `monthly/{id}.json`，版式同今日 |
| `oss` | 开源榜 | `opensource/latest.json` |
| `board` | 模型榜 | 现有 `leaderboard.json` |
| `stream` | 动态 | `stream/24h.json` 或 `stream/7d.json` |
| `library` | 一手库 | `frontend/sources/**/articles.json`（现「开发者中心」） |

砍掉作为一级导航的：精选 / 全部 AI 动态 / 热点榜 / AI 日报 / 主题 / 收藏。热点不再独立成栏；多源交叉只作为稿件的 `also_seen` 与可选 `hot_score`。收藏若保留，挂页脚，不占侧栏。

今日页结构：

```
┌ 前沿 · {上海日期} {周几} ─────────────────────────────────┐
│ 模型 n · 产品 n · 行业 n · 论文 n · 教程 n · 观点 n · 开源新上榜 n │
│ 数据截至 {HH:mm} 上海 · 一手源 x 路正常 · y 路失败         │
├ 左：今日简报（六区） ──────────┬ 右：今日开源 + 模型共识 ──┤
│ 01 模型 …                    │ GitHub 日榜官方 Top      │
│ 02 产品 …                    │ 共识分前 3 → ?v=board    │
│ … 空区显示「今日无一手更新」    │                          │
└──────────────────────────────┴──────────────────────────┘
```

从某区「看更多」进入动态时只预填 `form`，不带 `audience`。

---

## 4. 数据契约

时区一律 **Asia/Shanghai** 记日历日；时间戳 ISO-8601 带偏移。  
旧字段 `items[]`、`topics`、`official`、`site_id` **保留**。前端优先读新字段；整份 JSON 没有 `schema: "content_hub_v1"` 时走旧渲染，保证历史日期能开。

### 4.1 稿件（对外卡片）

`slim()` 必须写出：

```json
{
  "id": "story_2026-08-19_anthropic-contain-claude",
  "title": "我们如何在各产品中约束 Claude",
  "title_en": "How we contain Claude across products",
  "url": "https://www.anthropic.com/engineering/how-we-contain-claude",
  "canonical_url": "https://www.anthropic.com/engineering/how-we-contain-claude",
  "origin": "Anthropic",
  "origin_key": "anthropic",
  "source": "Anthropic",
  "site_id": "official_ai",
  "source_class": "first_party",
  "official": true,
  "form": "tutorial",
  "audience": ["engineering"],
  "tracks": ["anthropic", "agent"],
  "published_at": "2026-08-17T00:00:00+08:00",
  "summary": "……2 至 4 句事实……",
  "reason": "……一句为什么今天值得读……",
  "source_count": 2,
  "also_seen": [
    { "origin": "Simon Willison", "url": "https://simonwillison.net/…" }
  ],
  "score": 78,
  "hot_score": 0,
  "classify_uncertain": false,
  "reading": {
    "vendor": "anthropic",
    "kind": "engineering",
    "slug": "how-we-contain-claude"
  },
  "model_id": null
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `form` | 六选一：`launch` `product` `industry` `paper` `tutorial` `opinion`。与筛栏、六区 id 相同。不再用 `agent` 当产品、`mcp` 当观点、`tips` 当教程。 |
| `audience` | 子集：`engineering` `research` `industry`。可空数组。筛「工程」= 数组包含 `engineering`。 |
| `source_class` | `first_party` / `specialist` / `aggregator`。由**发布方域名与白名单**决定，不是 `site_id`。 |
| `official` | **派生字段**：仅 `source_class === "first_party"` 时为 `true`。禁止再用标题里的 Anthropic 或 `site_id=official_ai` 抬旗。 |
| `origin` / `origin_key` | 可信发布方展示名与稳定 slug。禁止写 buzzing.cc、newsnow、iris、techurls、aihot。 |
| `source` | 对外等于 `origin`。旧前端读 `source` 也能对。 |
| `tracks` | 现有主题 slug，多选。不进动态筛栏。 |
| `also_seen` | 合并进来的其它报道，不含代表条自己。 |
| `reading` | 仅当该 URL 已在知识源 `articles.json` 且 `status=translated` 时填写 `{vendor,kind,slug}`；否则 `null`。前端自行拼阅读器地址，管线不写死 HTML 或 Next 路径。 |
| `model_id` | 仅当能对上 `leaderboard.json` 的型号 id 时填写；对不上不硬连。 |
| `classify_uncertain` | `true` 时不进六区配额，可进动态；动态「类别」维选具体 form 时排除它。 |

兼容：一个版本内 `?type=product` 同时认旧值 `agent`；`?type=tutorial` 认旧值 `coding`；`?type=opinion` 认旧值 `mcp` 与 `tips`。只认 URL，新数据不再写旧 id。

### 4.2 今日包 `dailies/{date}.json`

```json
{
  "schema": "content_hub_v1",
  "generated_at": "2026-08-19T05:17:00+00:00",
  "date": "2026-08-19",
  "timezone": "Asia/Shanghai",
  "sealed": false,
  "selection": "section_quota_v1",
  "lead": "一句话刊头，可空",
  "source_health": { "ok": 11, "failed": 2, "as_of": "2026-08-19T13:17:00+08:00" },
  "counts": { "launch": 1, "product": 2, "industry": 0, "paper": 1, "tutorial": 1, "opinion": 0 },
  "sections": [
    { "id": "launch", "label": "模型发布 / 更新", "items": [] },
    { "id": "product", "label": "产品 / 工具发布", "items": [] },
    { "id": "industry", "label": "行业动态", "items": [] },
    { "id": "paper", "label": "论文研究", "items": [] },
    { "id": "tutorial", "label": "教程 / 指南", "items": [] },
    { "id": "opinion", "label": "观点", "items": [] }
  ],
  "items": [],
  "opensource": { "date": "2026-08-19", "items": [] },
  "models_glance": { "updatedAt": "", "items": [] },
  "hot": []
}
```

- `sections[].items`：过完整度门 + 分区配额后的公开面。
- `items`：六区并集，顺序为区序再区内原序。旧「精选」流继续读它。
- `sealed: true` 后禁止覆盖除 `source_health.as_of` 以外的内容（见第 8 节）。
- `opensource.items`：当日 GitHub 日榜最多 8 条，字段见 4.4。
- `models_glance.items`：共识分前 3，字段：`id` `name` `score` `completeness`。

### 4.3 动态包 `stream/24h.json` / `stream/7d.json`

```json
{
  "schema": "content_hub_v1",
  "window": "24h",
  "generated_at": "",
  "timezone": "Asia/Shanghai",
  "items": [],
  "facets": {
    "form": [{ "id": "paper", "label": "论文", "count": 12 }],
    "source_class": [{ "id": "first_party", "label": "一手", "count": 9 }],
    "origin": [{ "id": "anthropic", "label": "Anthropic", "count": 7 }],
    "audience": [{ "id": "engineering", "label": "工程实践", "count": 15 }]
  }
}
```

`items` 是过硬门、未按六区配额截断的稿件流（可含 `classify_uncertain`）。  
`facets` 按**当前时间窗全量**统计，不随其它维缩小（避免点了「论文」后出处列表被掏空无法改选）。  
出处超过 12 个时，前端只展示前 12 +「更多」，JSON 仍给全量。

硬门（进动态，宽于今日公开面）：

- 有规范中文或英文标题、规范 URL、`origin`、`source_class`、`published_at`
- AI 相关：沿用现网 `ai_is_related`（阈值 0.65）或 `ai_score ≥ 0.65`
- 不是仓库对象、不是榜单行

今日公开面额外要求：`summary`（≥ 40 字）、`reason`（≥ 8 字）、`form` 已定且 `classify_uncertain=false`。

### 4.4 开源对象 `opensource/{date}.json` 与 `opensource/latest.json`

```json
{
  "schema": "content_hub_v1",
  "date": "2026-08-19",
  "github_trending": {
    "ok": true,
    "url": "https://github.com/trending?since=daily",
    "items": [
      {
        "rank": 1,
        "repo": "owner/name",
        "url": "https://github.com/owner/name",
        "desc": "",
        "stars_today": "1,200",
        "language": "",
        "agent_related": true
      }
    ]
  },
  "trendshift": {
    "ok": true,
    "source": "https://trendshift.io/",
    "periods": {
      "daily": { "ok": true, "items": [] },
      "weekly": { "ok": true, "items": [] },
      "monthly": { "ok": true, "items": [] },
      "yearly": { "ok": true, "items": [] }
    }
  }
}
```

- GitHub 日榜：**官方顺序 Top 20**，不按 Agent 关键词重排或剔除。`agent_related` 只作角标。
- Trendshift：保留官方 `rank`、`stars`、`stars_gained`、`tags`。
- 抓取失败：`ok=false`，`items=[]`，页上写「本轮未取到官方榜」，不拿旧日榜冒充今日。

实现：把 `way-to-agentic/scripts/frontier_fetch.py` 里已有的 `fetch_github_trending` / `fetch_all_trendshift` 迁入雷达仓 `scripts/fetch_opensource.py`（或薄封装调用），由 `update_news.py` 主流程调用并落盘。不再依赖学习站 `daily/raw` 里经常 `skipped: true` 的快照。

### 4.5 模型榜

继续消费现有 `frontend/frontier/radar-data/leaderboard.json`。本方案不改 `groups` / `sources` / 共识分。  
今日只引用 `models[:3]` 的 `id` `name` `score` `completeness` 与包级 `updatedAt`。  
稿件 `model_id` 能对上时，标题旁给 `?v=board&model=`。

### 4.6 精读索引（知识源，不新造库）

权威路径不变：

```
frontend/sources/{vendor}/{kind}/articles.json
frontend/sources/{vendor}/{kind}/articles/{slug}.md
frontend/sources/{vendor}/{kind}/raw/{slug}.txt
```

`articles.json` 元素：

```json
{
  "slug": "how-we-contain-claude",
  "title": "How we contain Claude across products",
  "titleZh": "我们如何在各产品中约束 Claude",
  "publishedAt": "2026-08-17",
  "url": "https://www.anthropic.com/engineering/how-we-contain-claude",
  "status": "translated"
}
```

一手库与今日短卡都只引用这些字段。学习中心课文继续只写路径，不复制译文。

### 4.7 发布方注册表 `feeds/origin_registry.json`（新文件）

管线与前端共用同一份名单，禁止再靠 `OFFICIAL_SOURCE_HINTS` 扫标题。

```json
{
  "hosts": {
    "www.anthropic.com": { "origin_key": "anthropic", "origin": "Anthropic", "class": "first_party" },
    "www.openai.com": { "origin_key": "openai", "origin": "OpenAI", "class": "first_party" },
    "developers.openai.com": { "origin_key": "openai", "origin": "OpenAI", "class": "first_party" },
    "openai.com": { "origin_key": "openai", "origin": "OpenAI", "class": "first_party" },
    "platform.claude.com": { "origin_key": "anthropic", "origin": "Anthropic", "class": "first_party" },
    "api-docs.deepseek.com": { "origin_key": "deepseek", "origin": "DeepSeek", "class": "first_party" },
    "www.deepseek.com": { "origin_key": "deepseek", "origin": "DeepSeek", "class": "first_party" },
    "www.llamaindex.ai": { "origin_key": "llamaindex", "origin": "LlamaIndex", "class": "first_party" },
    "langfuse.com": { "origin_key": "langfuse", "origin": "Langfuse", "class": "first_party" },
    "cohere.com": { "origin_key": "cohere", "origin": "Cohere", "class": "first_party" },
    "qwenlm.github.io": { "origin_key": "qwen", "origin": "Qwen", "class": "first_party" },
    "huggingface.co": { "origin_key": "huggingface", "origin": "Hugging Face", "class": "specialist" },
    "simonwillison.net": { "origin_key": "simonwillison", "origin": "Simon Willison", "class": "specialist" },
    "www.langchain.com": { "origin_key": "langchain", "origin": "LangChain", "class": "first_party" },
    "blog.google": { "origin_key": "google", "origin": "Google", "class": "first_party" },
    "deepmind.google": { "origin_key": "deepmind", "origin": "DeepMind", "class": "first_party" },
    "research.google": { "origin_key": "google", "origin": "Google Research", "class": "first_party" },
    "devblogs.microsoft.com": { "origin_key": "microsoft", "origin": "Microsoft", "class": "first_party" },
    "developer.nvidia.com": { "origin_key": "nvidia", "origin": "NVIDIA", "class": "first_party" },
    "arxiv.org": { "origin_key": "arxiv", "origin": "arXiv", "class": "specialist" }
  },
  "aggregator_hosts": [
    "buzzing.cc", "newsnow.busiyi.world", "techurls.com", "iris.xyz",
    "zeli.app", "news.ycombinator.com", "www.aibase.com", "www.aihubtoday.com"
  ],
  "aggregator_site_ids": [
    "techurls", "buzzing", "iris", "newsnow", "aibase", "aihubtoday",
    "bestblogs", "zeli", "hackernews"
  ]
}
```

解析顺序：

1. 取故事代表条的 `canonical_url`（优先官方 URL，而不是聚合页）。
2. host 命中 `hosts` → 用其 `origin` / `class`。
3. host 命中 `aggregator_hosts` 或 `site_id` 在 `aggregator_site_ids` → `source_class=aggregator`；若能从 aihot `source` 或文章最终 URL 解析出发行方，`origin` 用发布方，否则 `origin=未标注`、`origin_key=unlabeled`。
4. 其余个人博客 / 媒体 → `specialist`，`origin` 用注册表或 registrable domain 的展示名。

`AIHOT_FIRST_PARTY_SOURCE_NAMES` 只用于在 aihot 搬运条上**找回官方 URL / 官方源名**，找回后仍走注册表，不再把整条 `site_id=aihot` 抬成 official。

---

## 5. 端到端管线

保持现有 Actions 骨架，在分类与编辑之间插入新步骤：

```
update-news.yml  (cron 17 * * * * UTC)
  1. update_news.py
       collect_all（现有源 + 第一期 8 个一手 HTML/RSS）
       + Follow Builders 公开 JSON（已有）
       + 付费社交（有 key 且过间隔才跑）
       + fetch_opensource.py
       归档 archive.json（21 天工作集，不当事后档案）
       AI 相关过滤（阈值不变）
       故事合并（URL / 6h 标题相似）
       resolve_origin()          ← 新
       classify_story()          ← 新
       补全 summary / reason     ← 收紧 slim 门
  2. build_editorial.py
       若昨日未封存 → seal_daily(yesterday)
       写今日 dailies（sealed=false）
       写 stream/24h、stream/7d
       写 opensource/latest 与 opensource/{date}
       从已封存日报重算 weekly / monthly（index 全量保留）
       写 hub.json
  3. persona_score.py           仍只打 daily-brief / TOP3，不改六区入选
  4. sync_knowledge_sources.py  写入学习站 frontend/sources（已有）
  5. 镜像雷达 JSON + sources 到 Gitee way-to-agentic
```

`persona_score` 继续服务 Skill / 锐评，**不决定**今日六区谁上榜。

### 5.1 第一期补的一手抓取器

在 `update_news.py` 增加独立 site_id（失败只记账），名单来自学习站 `vendors.yaml` P0/P1 缺口：

| site_id | 源 | 已有能力 |
|---|---|---|
| `anthropic_engineering` | https://www.anthropic.com/engineering | `sync_knowledge_sources.list_engineering` / `frontier_fetch.parse_anthropic_engineering` |
| `anthropic_cookbook` | Claude Cookbook 列表 | 知识源同步已有 |
| `anthropic_news` | Anthropic News | 知识源 / frontier 已有 |
| `openai_cookbook` | developers.openai.com/cookbook | 知识源已有；列表失败可读本地 `articles.json` |
| `deepseek_updates` | api-docs.deepseek.com/updates + Harness 页 | `frontier_fetch` 已有解析 |
| `llamaindex_blog` | llamaindex.ai/blog | 需从 HTML 列表接入 |
| `langfuse_blog` | langfuse.com/blog | 需从 HTML/RSS 接入 |
| `cohere_blog` | cohere.com/blog | 需从 HTML 列表接入 |

约束（与现网单源策略一致）：超时 20s、最多 2 次、解析 0 条记 `ok: true, item_count: 0`；选择器失效记 `error: parse_empty`。不拖垮 55 分钟超时。

这些抓取器产出的是**稿件**（标题、URL、日期）。Engineering / Cookbook 的全文翻译仍只由 `sync_knowledge_sources.py` 写进 `frontend/sources/`。雷达侧若发现对应 slug 已译，写 `reading` 坐标。

### 5.2 故事合并（沿用，补代表条规则）

现网：同规范 URL，或 6 小时内标题相似度 ≥ 0.86。  
代表条选择改为：

1. `source_class=first_party` 且 URL 非聚合域，优先。
2. 其次 `specialist`。
3. 聚合站只作 `also_seen`，除非没有任何一手/专业 URL。

合并后 `origin` 取代表条；`source_count` = 不同 `origin_key` 数（不是不同 `site_id` 数）。

### 5.3 重要性分（入动态排序，不决定六区资格）

现网公式可保留作 `score`：

`0.3*editorial + 0.22*source_tier + 0.2*ai + 0.18*recency + 0.1*heat`

但六区资格**不再**用 `score ≥ 0.72 或 多源 ≥ 2`。改为完整度门 + 分区配额。`BRIEF_SCORE_GATE` 只留给 `daily-brief.json`（Skill 兼容），不喂今日页。

`source_tier` 仍可作抓取优先级；对外身份只认 `source_class`。

---

## 6. 分类 `scripts/classify_story.py`（新）

输入：已合并故事（含 `origin`、标题、摘要、URL）。  
输出：`form`、`audience[]`、`tracks[]`、`classify_uncertain`、`model_id`（可选）。

### 6.1 规则层（先跑，可单测）

按 URL 与标题关键词，命中则锁定，不再调用 LLM：

| 条件 | form |
|---|---|
| host 为 `arxiv.org` 或路径含 `/pdf/` 且标题像论文 | `paper` |
| 标题/摘要含 发布、weights、context window、降价、API 型号名 + `origin` 是模型厂商 | `launch` |
| Changelog、Cookbook 新方、SDK、Claude Code / Codex / Cursor 产品更新 | `product` |
| 融资、并购、监管、算力、电力、董事会 | `industry` |
| Engineering / Cookbook / 「how to」「指南」「实践」 | `tutorial` |
| 博客评论、采访观点、无产品事实的专栏 | `opinion` |

`audience` 规则：

- `engineering`：Harness、工具、MCP、SDK、评测工程、生产事故
- `research`：论文、基准、对齐、评测方法
- `industry`：融资、政策、市场、组织

可多打。只打得上的才写入；打不上保持 `[]`。

`model_id`：用 `leaderboard.json` 的型号别名表做子串匹配；冲突（一条命中两个型号）则不写。

### 6.2 LLM 层（可选）

仅当规则层无法定 `form`，且 `DEEPSEEK_API_KEY` 存在时调用。Prompt 只允许输出 JSON：

```json
{ "form": "product", "audience": ["engineering"], "uncertain": false }
```

`uncertain=true` 或非法 `form` → 走失败路径。  
无密钥或超时：同一失败路径。  
失败路径：`form` 缺省为 `opinion`（仅占位）、`audience=[]`、`classify_uncertain=true`，**不进六区**。动态可见，类别维选「观点」时仍排除不确定项（避免脏数据充观点区）。

禁止用旧 `digestBucket` / `TOPICS` 的 form group 回填空区。

---

## 7. 今日分区配额

对每个 `form`，从当日上海日历日（及 36 小时内跨日但事件属今日的故事）中取 `classify_uncertain=false` 且过公开面完整度门的候选，排序：

1. `source_class=first_party` 优先
2. 不同 `origin` 交叉（`source_count` 大）优先
3. 同 `origin` 已占 2 条则降权 0.03（沿用多样性惩罚）
4. 标题近重复跳过（现网 0.86）

每区上限 **6** 条，六区合计建议 **18–24**。  
`aggregator` **不能单独填满一区**：若该区已有 `first_party` 或 `specialist`，聚合条不进区（仍可进动态）。若该区为零，**宁可空区**，不用聚合顶上。

`pick_daily()` 的「官方 10 + 交叉 8 + 非聚合 6、不足 12 用聚合补」**废止**。

---

## 8. 日封存与周月派生

### 8.1 日

- 工作中的今日文件：`dailies/{today}.json`，`sealed=false`，每小时可覆盖。
- 进入新的上海日后，下一轮 `build_editorial` 先把昨天的包标 `sealed=true`，并写 `dailies/{yesterday}.sealed.json` 字节级副本（防误覆盖）。
- 已封存日期：若文件存在且 `sealed=true`，跳过重写。
- `dailies/index.json`：保留全部日期，不再只留 90 天（磁盘上单日 JSON 很小）。若必须裁，只裁 `archive.json`，不裁日历报。

### 8.2 周 / 月

- 周 id：ISO 周 `YYYY-Www`（已有 `2026-W34`）。
- 月 id：`YYYY-MM`。
- 组装：读取该周期内**已封存**日报的 `sections`，按 `form` 合并去重（同一 `canonical_url` 只留一次），每区最多 12 条。
- 进行中的本周 / 本月：用已封存日 + 当日未封存包，产物标 `sealed=false`。
- `weekly/index.json` / `monthly/index.json`：追加，不删旧项。

禁止再把近 7 个 `dailies` 扁平 `items` 截 40 条当周报。

### 8.3 archive

`archive.json` 仍 21 天，只当去重与补抓工作集。对外历史以 `dailies/` `weekly/` `monthly/` `opensource/` 为准。

---

## 9. 动态五维筛选

前端状态（写入 URL，可分享）：

| 参数 | 维 | 缺省 |
|---|---|---|
| `form` | 类别 | 空 = 全部 |
| `class` | 出处层级 | 空 = 全部 |
| `origin` | 出处 | 空 = 全部 |
| `q` | 搜索 | 空 |
| `audience` | 角色 | 空 = 全部 |
| `window` | 时间窗（范围，不是内容维） | `24h` |

语义：维与维 **AND**；单维第一期 **单选**。  
搜索对 `title` `title_en` `summary` `origin` 做大小写不敏感包含。

匹配：

```
item.form === form
item.source_class === class
item.origin_key === origin
audience ∈ item.audience
window 内 published_at
```

`origin=unlabeled` 可选，用来修数据，不默认展示在前 12 出处里（可放「更多」末尾）。

旧快照没有 `form` / `source_class` / `origin_key` / `audience` 时：对应维控件 `disabled`，提示「此窗口无结构化标签」；搜索与时间窗仍可用。禁止回退 `TYPE_HINTS`。

结果行：`{n} 条 · {已选维的中文} · {窗}`，一键清空除 `window` 外的维。

数据：`window=24h` 读 `stream/24h.json`（过硬门故事，不再用 `latest-24h.json` 截 80 条）。`7d` 读 `stream/7d.json`。前端不再本地截断后再筛。

---

## 10. 一手库与知识源

### 10.1 关系

| 层 | 职责 |
|---|---|
| 知识源模块 | 正文唯一落点。官方更新 → 按现有译者约定译成 Markdown → 写 `frontend/sources/` |
| 首页一手库 | 目录。读 `articles.json`，不复制 md |
| 今日短卡 | 可选引用。Engineering 新文最多一条稿件，点进同一阅读器 |

### 10.2 翻译约定（已存在，必须遵守）

`sync_knowledge_sources.translate_full`：

- 文首两行：`> 原文：[title](url)` 与 `> 译文说明：…`
- 全文翻译，非摘要
- 保留标题层级、列表、代码块；代码与标识符不译
- 术语首次可中英并列
- `TRANSLATE_USE_DEEPSEEK=1` 且有密钥时用 DeepSeek；否则 Google 分段
- 仅 `FULLTEXT_SOURCE_IDS` 四路全文：`engineering` `cookbook` `openai_cookbook` `langchain_blog`
- 每源 `KNOWLEDGE_MAX_NEW`（默认 2），整轮 `KNOWLEDGE_MAX_NEW_TOTAL`（默认 4）

其它厂商在一手库只展示标题；未译点官方 URL。要扩全文翻译，先加站内阅读器，再加入 allowlist。

### 10.3 点击跳转

| 状态 | 雷达静态页 | Next 学习站 |
|---|---|---|
| 已译 | `../sources/{vendor}/{kind}.html#{slug}` | `/zh/discover/sources/{vendor}/{kind}/{slug}` |
| 未译 | `row.url` 新开页 | 同左 |

Next 的 `sources/[vendor]/[kind]/[slug]/page.tsx` 本方案要求改为**读取对应 md**（或构建时注入），不再用 Penpot 占位标题。静态阅读器保持可用，直到 Next 阅读页通过验收。

今日稿件若 `reading` 有值，主按钮进站内精读（各前端按 10.3 拼 URL），次链保留原文。

---

## 11. 前端改动

### 11.1 现网雷达页（第一期必做）

文件：`way-to-agentic/frontend/frontier/radar-page.js`

- `NAV_VIEWS` 改为第 3 节六档；`dev` 改名为 `library`，query `?v=library` 同时认 `dev`。
- `viewToday`：读 `dailies/{date}.json` 的 `sections`；无 `sections` 才走旧扁平 `items`。
- 删除 `digestBucket` 关键词分桶（仅旧包回退）。
- `viewStream`：读 `stream/{window}.json`，五维 + 时间窗。
- `viewOss`：新，读 `opensource/latest.json`。
- `viewBoard`：保持现逻辑。
- `viewLibrary`：现 `viewDev`，卡片 href 规则不变。
- `matchItem`：按 `form` / `source_class` / `origin_key` / `audience` 精确匹配。
- `TYPES` 与六档对齐，去掉 `agent`/`mcp` 作为正式 id。

镜像清单：Actions 现拷 `daily-brief` `stories-merged` `dailies` `hot` `topics` `weekly` `monthly` `hub`。追加 `stream/`、`opensource/`。`latest-24h.json` 可继续写，但动态页不再读它。

### 11.2 Next `apps/web`（第二期，契约已预留）

| 路由 | 对应当前 / 新栏目 |
|---|---|
| `/[locale]/discover/frontier` | 今日 |
| `/[locale]/discover/frontier/[date]` | 某日 |
| `/[locale]/discover/timeline` | 动态（接五维 query） |
| `/[locale]/discover/sources` | 一手库 |
| `/[locale]/discover/sources/[vendor]/[kind]/[slug]` | 精读 |

第二期再加 `/discover/oss`、`/discover/board`。第一期用静态雷达页即可验收 S1–S10。

---

## 12. 失败与降级

| 失败 | 行为 |
|---|---|
| 单源超时 / 403 / 改版 | `source-status` 记 `ok: false`；其它源继续 |
| 开源榜抓取失败 | 今日右侧开源条隐藏或「未取到」；不影响六区 |
| 知识源同步失败 | Actions 已有 `::warning`；雷达快照照发 |
| 分类失败 / 无 DeepSeek | `classify_uncertain=true`，不进六区 |
| 摘要/理由补全失败 | 不进六区；可进动态 |
| 缺 `DEEPSEEK` | 标题翻译仍走 Google（现网）；知识源全文走 Google；摘要门导致今日更短，接受宁缺 |
| 旧 `dailies` 无 `sections` | 今日用扁平 `items` + 旧 UI；动态结构化维禁用 |

---

## 13. 文件与职责

### 13.1 雷达仓（新建）

| 文件 | 职责 |
|---|---|
| `feeds/origin_registry.json` | 发布方 host → origin / class |
| `scripts/origin.py` | 解析 origin、强制 aggregator 非官方 |
| `scripts/classify_story.py` | form / audience / model_id |
| `scripts/fetch_opensource.py` | GitHub Trending + Trendshift |
| `scripts/seal_calendar.py` | 封存日、派生周月（也可并入 `build_editorial.py`） |

### 13.2 雷达仓（修改）

| 文件 | 改什么 |
|---|---|
| `scripts/update_news.py` | 8 个一手抓取器；合并后调 origin + classify；代表条规则 |
| `scripts/build_editorial.py` | 废止 `is_official` 提示词扫描；新 `slim()`；`sections`；封存；stream；opensource；周月派生 |
| `.github/workflows/update-news.yml` | 拷贝 `stream/` `opensource/` |
| `tests/test_daily_brief.py` | 六区资格不再用 0.72 门 |
| `tests/test_origin.py` | 新：聚合站不能变一手 |
| `tests/test_classify_story.py` | 新：规则层 |
| `tests/test_editorial_sections.py` | 新：空区、完整度、封存 |
| `tests/test_stream_facets.py` | 新：AND 与 facet 计数 |

### 13.3 学习站（修改）

| 文件 | 改什么 |
|---|---|
| `frontend/frontier/radar-page.js` | 六档导航、今日分区、动态五维、开源榜、一手库改名 |
| `apps/web/app/.../sources/[slug]/page.tsx` | 第二期读 md |
| `docs/功能设计/完整功能设计.md` | 导航与 P1-09/P1-12 对齐本契约（文档，不挡第一期） |

`update_news.py` 已超过 6800 行：新抓取器写成模块函数，由 `collect_all` 注册，避免再把分类逻辑堆进该文件中部。

---

## 14. 测试策略

先写失败测试再改生产代码。

| 用例 | 期望 |
|---|---|
| buzzing 条，URL 为 daringfireball | `source_class=aggregator`，`official=false`，`origin` 为 Daring Fireball 或未标注，不得为 buzzing |
| anthropic.com/engineering URL | `source_class=first_party`，`origin=Anthropic` |
| 缺 `reason` 的故事 | 不出现在 `sections[*].items` |
| `classify_uncertain=true` | 不在任一区 |
| 聚合-only 候选、该区已有一手 | 聚合不进区 |
| 六区皆空 | JSON 仍有 6 个 section，items 空 |
| `seal_daily` 后再次 `build_editorial` | 封存文件字节不变（或 `sealed` 内容哈希不变） |
| 周报 | 只含已封存日的 `canonical_url` 并集 |
| 动态 `form=paper&class=first_party&origin=anthropic&audience=engineering` | 四条件全满足；facet 计数仍相对全窗 |
| 旧 daily 无 `form` | 动态类别维禁用 |
| GitHub 榜 items[0].rank == 1 且顺序与官方一致 | 不按 `agent_related` 重排 |
| 已译 slug | `reading` 为 `{vendor,kind,slug}`；未译为 null |

现有 `test_story_merge.py`、`test_ai_relevance.py` 保持绿。`test_daily_brief.py` 里依赖 `is_official` / 0.72 门的断言改为新契约。

---

## 15. 发布顺序

不改 workflow 四段顺序。代码按可验收切片上线：

1. **契约与注册表**：`origin.py` + `classify_story` 规则层 + 测试。尚不改页面。
2. **slim / sections / stream JSON**：`build_editorial` 写出新字段；旧前端忽略未知字段，不坏。
3. **8 个一手抓取器**：`source-status` 可见。
4. **封存与周月**：index 全量保留。
5. **开源抓取落盘**。
6. **雷达页六档 + 动态五维 + 今日分区**。
7. **一手库改名与 `reading` 坐标**（知识源同步已在跑，只接线）。
8. Next 精读页读 md（可与 6 并行，不挡雷达页验收）。

每步都要雷达仓 pytest 通过后再镜像。

---

## 16. 明确废止的旧行为

| 旧行为 | 代替 |
|---|---|
| `is_official()` 用 openai/anthropic/github 提示词扫标题 | `origin_registry` + URL host |
| `official_first_then_crossing` 凑 20 条 | `section_quota_v1` + 完整度门 |
| `daily-brief` 0.72 门当今日资格 | 仅 Skill 包 |
| 动态读 `latest-24h.json` 前 80 条 | `stream/24h.json` 全量过硬门 |
| 类型猜 `TYPE_HINTS` | 只认 `form` |
| 来源下拉「一手 / 资讯」 | `source_class` + `origin` |
| 周/月 index 只留 latest | 全量追加 |
| 把仓库、issue、Product Hunt 当官方稿 | 开源对象或剔除 |
| 知识源正文写入雷达卡片 | `reading` 坐标 |

---

## 17. 自检

- 无 TBD：四类对象、六档导航、五维参数、封存规则、8 个源、完整度门、跳转表均已定值。
- 内部一致：`form` 六值在分类、配额、筛栏、空区链接中相同；`official` 只由 `first_party` 派生；一手库不存第二份正文。
- 范围：一份实施计划可覆盖雷达仓管线 + 静态雷达页；Next 精读标为第 8 步，不挡 S1–S10。
- 歧义已钉死：出处 = 发布方不是抓取站；时间窗不是第六内容维；角色可多标、筛时包含匹配；聚合不得填满有一手的区。
