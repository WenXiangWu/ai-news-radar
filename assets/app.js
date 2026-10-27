const state = {
  itemsAi: [],
  itemsAll: [],
  itemsAllRaw: [],
  creatorItemsAi: [],
  creatorItemsAll: [],
  creatorWindowDays: 7,
  statsAi: [],
  totalAi: 0,
  totalRaw: 0,
  totalAllMode: 0,
  allDedup: true,
  allDataLoaded: false,
  allDataUrl: "data/latest-24h-all.json",
  allDataPromise: null,
  siteFilter: "",
  authorFilter: "",
  query: "",
  mode: "selected",
  waytoagiMode: "today",
  waytoagiData: null,
  sourceStatus: null,
  generatedAt: null,
  dailyBrief: null,
  top3Personas: null,
  storiesMerged: null,
  storiesDataUrl: "data/stories-merged.json",
  activeSection: "all",
  mainListVisibleCount: 0,
  xAuthorsExpanded: false,
};

function resolveDataBaseUrl() {
  let fromQuery = "";
  try {
    fromQuery = new URLSearchParams(window.location.search).get("data") || "";
  } catch {
    fromQuery = "";
  }
  if (fromQuery) {
    const normalized = fromQuery.trim().replace(/\/+$/, "");
    try { localStorage.setItem("dataBaseUrl", normalized); } catch {}
    return normalized;
  }
  try {
    const stored = localStorage.getItem("dataBaseUrl") || "";
    return stored.trim().replace(/\/+$/, "");
  } catch {
    return "";
  }
}

state.dataBaseUrl = resolveDataBaseUrl();

function dataUrl(path) {
  const base = state.dataBaseUrl;
  if (!base) return path;
  const file = String(path || "").split("/").pop();
  return `${base}/${file}`;
}

const newsListEl = document.getElementById("newsList");
const searchInputEl = document.getElementById("searchInput");
const itemTpl = document.getElementById("itemTpl");
const modeSelectedBtnEl = document.getElementById("modeSelectedBtn");
const modeAllBtnEl = document.getElementById("modeAllBtn");
const hotListEl = document.getElementById("hotList");
const hotBoardEl = document.getElementById("hotBoard");
const fullListWrapEl = document.getElementById("fullListWrap");
const flTitleEl = document.getElementById("flTitle");
const flCountEl = document.getElementById("flCount");
const loadMoreBtnEl = document.getElementById("loadMoreBtn");
const dailyBriefEl = document.getElementById("dailyBrief");
const dbTextEl = document.getElementById("dbText");
const dbDateEl = document.getElementById("dbDate");
const dbStatSignalsEl = document.getElementById("dbStatSignals");
const dbStatCuratedEl = document.getElementById("dbStatCurated");
const dbStatSourcesEl = document.getElementById("dbStatSources");
const dbStatHotEl = document.getElementById("dbStatHot");
const teemoQuoteEl = document.getElementById("teemoQuote");
const teemoStatsEl = document.getElementById("teemoStats");
const healthRingEl = document.getElementById("healthRing");
const healthPercentEl = document.getElementById("healthPercent");
const healthLabelEl = document.getElementById("healthLabel");
const healthItemsEl = document.getElementById("healthItems");
const activityGridEl = document.getElementById("activityGrid");
const tbTitleEl = document.getElementById("tbTitle");
const tbTimeEl = document.getElementById("tbTime");
const sbDotEl = document.getElementById("sbDot");
const sbSourceStatusEl = document.getElementById("sbSourceStatus");
const sbUpdatedAtEl = document.getElementById("sbUpdatedAt");
const sidebarEl = document.getElementById("sidebar");
const menuBtnEl = document.getElementById("menuBtn");
const sidebarOverlayEl = document.getElementById("sidebarOverlay");
const newsCard1El = document.getElementById("newsCard1");
const newsCard2El = document.getElementById("newsCard2");
const hotMoreLinkEl = document.getElementById("hotMoreLink");

const cntEls = {
  all: document.getElementById("cntAll"),
  selected: document.getElementById("cntSelected"),
  hot: document.getElementById("cntHot"),
  models: document.getElementById("cntModels"),
  products: document.getElementById("cntProducts"),
  devtools: document.getElementById("cntDevtools"),
  community: document.getElementById("cntCommunity"),
  research: document.getElementById("cntResearch"),
};

const SOURCE_KINDS = {
  official_ai: { label: "官方", tone: "official" },
  curated_media: { label: "精选媒体", tone: "aihub" },
  aihot: { label: "AI HOT", tone: "hot" },
  aibreakfast: { label: "日报", tone: "newsletter" },
  followbuilders: { label: "Builders/X", tone: "builders" },
  xapi: { label: "X API", tone: "builders" },
  socialdata_x: { label: "X 搜索", tone: "builders" },
  tikhub_douyin: { label: "抖音", tone: "creator" },
  tikhub_xiaohongshu: { label: "小红书", tone: "creator" },
  techurls: { label: "聚合", tone: "aggregate" },
  buzzing: { label: "聚合", tone: "aggregate" },
  iris: { label: "聚合", tone: "aggregate" },
  bestblogs: { label: "博客", tone: "blogs" },
  zeli: { label: "聚合", tone: "aggregate" },
  hackernews: { label: "HN", tone: "aggregate" },
  aihubtoday: { label: "AI站点", tone: "aihub" },
  aibase: { label: "AI站点", tone: "aihub" },
  waytoagi: { label: "社区", tone: "builders" },
  newsnow: { label: "聚合", tone: "aggregate" },
  opmlrss: { label: "OPML", tone: "newsletter" },
};

const AIHOT_SUB_LABELS = { x: "X", wechat: "公众号", hn: "HN", rss: "RSS" };
const AIHOT_SUB_TONES = { x: "builders", wechat: "creator", hn: "aggregate", rss: "newsletter" };

const SECTION_DEFS = [
  { id: "all", label: "全部", short: "全部", description: "不筛选内容栏目，查看全部信号" },
  { id: "models", label: "模型", short: "模型", description: "模型发布、能力升级、评测与开源权重" },
  { id: "products", label: "产品", short: "产品", description: "AI 应用、Agent、生成工具和用户产品更新" },
  { id: "devtools", label: "开发者", short: "开发者", description: "编程工具、API、开源项目、推理与工程实践" },
  { id: "industry", label: "行业", short: "行业", description: "公司战略、融资收购、监管、芯片与产业变化" },
  { id: "research", label: "论文", short: "论文", description: "论文、基准、方法、数据集与研究团队动态" },
  { id: "community", label: "社区", short: "社区", description: "HN、中文技术社区与社群动态" },
  { id: "creator", label: "自媒体", short: "自媒体", description: "抖音、小红书等自媒体创作者内容" },
];

const SECTION_BY_ID = Object.fromEntries(SECTION_DEFS.map((section) => [section.id, section]));

const TEEMO_QUOTES = [
  "我正在替你盯着所有信源，稍后给你汇报。",
  "今天的信号比昨天多，我来帮你划重点。",
  "侦察完毕！以下是今日最值得关注的动态。",
  "有几个信源今天特别活跃，我已经标出来了。",
  "提莫报告：一切尽在掌握之中。",
  "种蘑菇之余，我也在帮你追踪 AI 前沿。",
];

function fmtNumber(n) {
  return new Intl.NumberFormat("zh-CN").format(n || 0);
}

const UNSAFE_HARD_PATTERNS = [
  /\bcreampie\b/i,
  /\bblowjob\b/i,
  /\bsuck (?:your|my) (?:dick|cock)\b/i,
  /中出|婊子|吸你的鸡鸡|操虚拟女友/i,
];

const UNSAFE_PROMO_PATTERNS = [
  /\b(?:nsfw|nudes?|porn(?:ography)?)\b/i,
  /\buncensored pictures?\b/i,
  /\bvirtual girlfriends?\b/i,
  /\bknock her up\b/i,
  /未经审查的图片|虚拟女友|色情内容|成人内容/i,
];

function contentSafetyText(record) {
  return [
    record?.title,
    record?.title_zh,
    record?.title_en,
    record?.title_original,
    record?.source,
    record?.source_name,
  ].filter(Boolean).join(" ");
}

function isUnsafeContent(record) {
  const text = contentSafetyText(record);
  if (!text) return false;
  if (UNSAFE_HARD_PATTERNS.some((pattern) => pattern.test(text))) return true;
  return UNSAFE_PROMO_PATTERNS.filter((pattern) => pattern.test(text)).length >= 2;
}

function safeItems(items) {
  return (Array.isArray(items) ? items : []).filter((item) => !isUnsafeContent(item));
}

function isUnsafeStory(story) {
  const refs = [
    story,
    story?.primary_item,
    ...(Array.isArray(story?.sources) ? story.sources : []),
    ...(Array.isArray(story?.items) ? story.items : []),
  ].filter(Boolean);
  return refs.some((ref) => isUnsafeContent(ref));
}

function fmtTime(iso) {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function fmtDate(iso) {
  if (!iso) return "未知日期";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

function fmtHHMM(ms) {
  if (!ms) return "--:--";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "--:--";
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}

function fmtRelativeTime(ms) {
  if (!ms) return "时间未知";
  const diff = Date.now() - ms;
  if (diff < 0) return "刚刚";
  const minutes = Math.round(diff / 60000);
  if (minutes < 60) return `${Math.max(1, minutes)} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}

function failedSourceCount(status = state.sourceStatus) {
  const failedSites = Array.isArray(status?.failed_sites) ? status.failed_sites.length : 0;
  const rss = status?.rss_opml || {};
  const failedFeeds = Array.isArray(rss.failed_feeds) ? rss.failed_feeds.length : 0;
  return failedSites + failedFeeds;
}

function modeLabelText() {
  return state.mode === "all" ? "全量" : "精选";
}

function sourceKind(siteId) {
  return SOURCE_KINDS[siteId] || { label: "来源", tone: "default" };
}

function sourceSignalTone(signal) {
  const text = String(signal || "").toLowerCase();
  if (text.includes("官方") || text.includes("official")) return "official";
  if (text.includes("ai hot") || text.includes("精选")) return "hot";
  if (text.includes("自媒体") || text.includes("tikhub") || text.includes("douyin") || text.includes("xiaohongshu") || text.includes("抖音") || text.includes("小红书")) return "creator";
  if (text.includes("builders") || text.includes("github") || text.includes("x")) return "builders";
  if (text.includes("aihub") || text.includes("aibase") || text.includes("媒体")) return "aihub";
  if (text.includes("hn") || text.includes("hacker") || text.includes("聚合")) return "aggregate";
  if (text.includes("opml") || text.includes("日报")) return "newsletter";
  return "default";
}

function siteRows() {
  return Array.isArray(state.sourceStatus?.sites) ? state.sourceStatus.sites : [];
}

function siteRow(siteId) {
  return siteRows().find((site) => site.site_id === siteId) || null;
}

function aiSiteStat(siteId) {
  const stats = safeAiSiteStats();
  return stats.find((site) => site.site_id === siteId) || null;
}

function safeAiSiteStats() {
  const visibleStats = computeSiteStats(safeItems(state.itemsAi));
  const visibleById = new Map(visibleStats.map((site) => [site.site_id, site]));
  const baseStats = Array.isArray(state.statsAi) && state.statsAi.length ? state.statsAi : visibleStats;
  return baseStats.map((site) => ({
    ...site,
    count: Number(visibleById.get(site.site_id)?.count || 0),
  }));
}

function computeSiteStats(items) {
  const m = new Map();
  items.forEach((item) => {
    if (!m.has(item.site_id)) {
      m.set(item.site_id, { site_id: item.site_id, site_name: item.site_name, count: 0, raw_count: 0 });
    }
    const row = m.get(item.site_id);
    row.count += 1;
    row.raw_count += 1;
  });
  return Array.from(m.values()).sort((a, b) => b.count - a.count || a.site_name.localeCompare(b.site_name, "zh-CN"));
}

function currentSiteStats() {
  if (state.mode === "all") return computeSiteStats(effectiveAllItems());
  return safeAiSiteStats().filter((site) => site.count > 0);
}

function creatorHotScore(item) {
  return normalizedPercent(item?.creator_hot_score);
}

function highPriorityScore(item) {
  if (itemSourceGroup(item) === "creator" && creatorHotScore(item)) return creatorHotScore(item);
  return scorePercent(item);
}

function isHighPriorityItem(item) {
  return highPriorityScore(item) >= 75 || itemPriorityScore(item) >= 82 || item.site_id === "official_ai" || item.site_id === "aihot";
}

function effectiveAllItems() {
  return safeItems(state.allDedup ? state.itemsAll : state.itemsAllRaw);
}

function repairDisplayedTitle(original, translated) {
  let result = String(translated || "").trim();
  const source = String(original || "");
  if (/\bCodex\b/i.test(source)) result = result.replaceAll("法典", "Codex");
  if (/\bBug Bounty\b/i.test(source)) result = result.replaceAll("错误赏金", "漏洞悬赏").replaceAll("Bug 赏金", "漏洞悬赏");
  if (/\bBio Bug Bounty\b/i.test(source)) result = result.replaceAll("生物漏洞悬赏", "生物安全漏洞悬赏");
  if (/\brepositor(?:y|ies)\b/i.test(source)) result = result.replaceAll("存储库", "代码仓库");
  if (/\bdesktop app\b/i.test(source)) result = result.replaceAll("桌面应用程序", "桌面应用");
  return result;
}

function isMostlyEnglishTitle(text) {
  const value = String(text || "").trim();
  const latin = (value.match(/[A-Za-z]/g) || []).length;
  const cjk = (value.match(/[㐀-鿿]/g) || []).length;
  return latin >= 4 && latin > cjk * 2;
}

function itemTitleText(item) {
  const zhTitle = item.title_enhanced_zh || item.title_zh || "";
  const preferred = (zhTitle || item.title || item.title_en || "未命名更新").trim();
  const titleParts = preferred.includes(" / ") ? preferred.split(" / ") : [];
  const display = !zhTitle && titleParts.length ? titleParts[0].trim() : preferred;
  const original = item.title_en || item.title_original || (titleParts.length > 1 ? titleParts.slice(1).join(" / ") : "");
  return repairDisplayedTitle(original, display);
}

function itemOriginalTitleText(item) {
  const explicit = String(item?.title_en || "").trim();
  if (isMostlyEnglishTitle(explicit) && explicit !== itemTitleText(item)) return explicit;
  const bilingual = String(item?.title || item?.title_bilingual || "").trim();
  if (bilingual.includes(" / ")) {
    const [, ...rest] = bilingual.split(" / ");
    const original = rest.join(" / ").trim();
    if (isMostlyEnglishTitle(original) && original !== itemTitleText(item)) return original;
  }
  const original = String(item?.title_original || "").trim();
  if (isMostlyEnglishTitle(original) && original !== itemTitleText(item)) return original;
  return "";
}

function itemSummaryText(item, maxLength = 180) {
  const text = String(item?.summary || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > maxLength ? `${text.slice(0, maxLength - 1).trim()}…` : text;
}

function scorePercent(item) {
  const score = Number(item.ai_score ?? item.score ?? 0);
  if (!Number.isFinite(score) || score <= 0) return 0;
  return Math.round(score <= 1 ? score * 100 : score);
}

function normalizedPercent(value) {
  const score = Number(value);
  if (!Number.isFinite(score) || score <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round(score <= 1 ? score * 100 : score)));
}

function scoreTone(score) {
  if (score >= 90) return "hot";
  if (score >= 75) return "strong";
  return "watch";
}

function itemLabelTone(item) {
  const label = item.ai_label || "";
  if (item.site_id === "official_ai") return "official";
  if (item.site_id === "aihot" || label === "curated_hotlist") return "hot";
  if (itemSourceGroup(item) === "creator") return "creator";
  if (label === "model_release") return "models";
  if (label === "developer_tool" || label === "developer_tooling" || label === "infrastructure" || label === "infra_compute") return "devtools";
  if (label === "research_paper") return "research";
  if (label === "industry_business") return "industry";
  if (label === "ai_product_update" || label === "agent_workflow" || label === "robotics") return "products";
  return "default";
}

function itemTagTone(label) {
  const text = String(label || "");
  if (text.includes("多源")) return "strong";
  if (text.includes("官方")) return "official";
  if (text.includes("精选") || text.includes("热点")) return "hot";
  if (text.includes("HN")) return "aggregate";
  if (text.includes("模型")) return "models";
  if (text.includes("开发")) return "devtools";
  if (text.includes("研究")) return "research";
  if (text.includes("自媒体")) return "creator";
  if (text.includes("社区")) return "community";
  if (text.includes("产品")) return "products";
  if (text.includes("行业")) return "industry";
  return "default";
}

function sourceSignal(item) {
  const site = item.site_name || "";
  const source = item.source || "";
  const hay = `${site} ${source}`.toLowerCase();
  if (site === "AI HOT") return "AI HOT精选";
  if (hay.includes("hackernews") || hay.includes("hacker news")) return "HN热议";
  if (source.includes("GitHub · Trending Today") || hay.includes("github")) return "GitHub趋势";
  if (site === "Official AI Updates") return "官方更新";
  if (site === "Follow Builders") return "Builders";
  if (site === "TikHub Douyin" || hay.includes("tikhub douyin")) return "抖音自媒体";
  if (site === "TikHub Xiaohongshu" || hay.includes("tikhub xiaohongshu")) return "小红书自媒体";
  if (site === "AIbase") return "AIbase";
  if (site === "OPML RSS") return "OPML";
  return site || "来源";
}

function itemSourceGroup(item) {
  const siteId = item.site_id || "";
  const aihotSub = aihotSubSource(item);
  if (aihotSub === "wechat") return "creator";
  if (aihotSub === "hn") return "community";
  const source = `${item.source || ""} ${item.site_name || ""}`.toLowerCase();
  if (
    siteId === "tikhub_douyin" ||
    siteId === "tikhub_xiaohongshu" ||
    source.includes("douyin") ||
    source.includes("xiaohongshu") ||
    source.includes("小红书") ||
    source.includes("抖音")
  ) return "creator";
  if (
    siteId === "hackernews" ||
    siteId === "hackernews" ||
    siteId === "zeli" ||
    siteId === "waytoagi" ||
    siteId === "followbuilders" ||
    siteId === "aibase" ||
    source.includes("hacker news") ||
    source.includes("hackernews") ||
    source.includes("hn algolia") ||
    source.includes("it之家") ||
    source.includes("36氪") ||
    source.includes("掘金") ||
    source.includes("readhub") ||
    source.includes("aibase") ||
    source.includes("公众号") ||
    source.includes("宝玉") ||
    source.includes("小互")
  ) return "community";
  return "other";
}

function aihotSubSource(item) {
  if (!item || item.site_id !== "aihot") return null;
  const source = String(item.source || "");
  if (source.includes("公众号")) return "wechat";
  if (/^X[:：]/.test(source)) return "x";
  if (source.includes("Hacker News")) return "hn";
  if (/（RSS）\s*$/.test(source) || /\(RSS\)\s*$/.test(source)) return "rss";
  return null;
}

function itemXAuthorSource(item) {
  if (!item) return null;
  if (item.site_id === "socialdata_x") return String(item.source || "").trim() || null;
  if (aihotSubSource(item) === "x") return String(item.source || "").trim() || null;
  return null;
}

function itemXAuthor(item) {
  const source = itemXAuthorSource(item);
  if (!source) return null;
  const match = source.match(/@([A-Za-z0-9_]+)/);
  return match ? `@${match[1]}` : null;
}

function itemXAuthorDisplay(item) {
  if (item?.site_id === "socialdata_x") {
    return String(item.source || "").trim() || null;
  }
  if (aihotSubSource(item) === "x") {
    return String(item.source || "").replace(/^X[:：]\s*/, "").trim() || null;
  }
  return null;
}

const AI_LABEL_SECTION_MAP = {
  model_release: "models",
  ai_product_update: "products",
  agent_workflow: "products",
  robotics: "products",
  developer_tool: "devtools",
  developer_tooling: "devtools",
  infra_compute: "devtools",
  research_paper: "research",
  industry_business: "industry",
};

const SECTION_FALLBACK_RULES = [
  ["research", [
    /paper|arxiv|research|benchmark|eval|dataset|lmsys|rdi|berkeley|huggingface daily papers|论文|研究|基准|评测|数据集|训练|k-means|speculative decoding/,
  ]],
  ["models", [
    /gpt[-\s]?\d|claude|gemini|grok|llama|qwen|deepseek|mistral|kimi\s?k\d|glm|gemma|模型|model|weights|权重|多模态|视频生成|diffusion|sora|seedance|llm|大模型/,
  ]],
  ["devtools", [
    /github|cursor|codex|copilot|openrouter|api|sdk|mcp|cli|framework|inference|推理|开发者|开源|代码|编程|算力|芯片|nvidia|cloud|部署|benchmarking|token/,
  ]],
  ["products", [
    /app|product|agent|workflow|siri|copilot|chatgpt|perplexity|runway|suno|支付宝|产品|应用|智能体|机器人|浏览器|搜索|助手|生成工具|办公|教育/,
  ]],
  ["industry", [
    /funding|raised|ipo|acquire|acquisition|lawsuit|regulation|policy|white house|pentagon|nvidia|salesforce|meta|microsoft|融资|收购|上市|监管|政策|裁员|估值|债券|芯片|公司|行业|政府|五角大楼|白宫/,
  ]],
];

function itemHaystack(item) {
  return [
    item.title,
    item.title_zh,
    item.title_en,
    item.title_original,
    item.source,
    item.site_name,
    item.site_id,
    item.ai_label,
    ...(Array.isArray(item.ai_signals) ? item.ai_signals : []),
  ].filter(Boolean).join(" ").toLowerCase();
}

function matchesAny(text, patterns) {
  return patterns.some((pattern) => pattern.test(text));
}

function itemSection(item) {
  const group = itemSourceGroup(item);
  if (group === "creator") return "creator";
  if (group === "community") return "community";
  const label = item.ai_label || "";
  const mapped = AI_LABEL_SECTION_MAP[label];
  if (mapped) return mapped;
  const hay = itemHaystack(item);
  for (const [sectionId, patterns] of SECTION_FALLBACK_RULES) {
    if (matchesAny(hay, patterns)) return sectionId;
  }
  return "all";
}

function itemMatchesSection(item, sectionId = state.activeSection) {
  return !sectionId || sectionId === "all" || itemSection(item) === sectionId;
}

function sectionBadgeLabel(sectionId) {
  return SECTION_BY_ID[sectionId]?.short || "栏目";
}

function storyRepresentativeItem(story) {
  if (!story) return null;
  if (story.primary_item && (story.primary_item.title || story.primary_item.url)) {
    const primary = story.primary_item;
    if (!primary.site_id && Array.isArray(story.sources)) {
      const match = story.sources.find((src) => src.url && src.url === primary.url) || story.sources[0];
      if (match) return { ...match, ...primary, site_id: match.site_id, site_name: match.site_name || match.source_name };
    }
    return primary;
  }
  if (Array.isArray(story.sources) && story.sources.length) return story.sources[0];
  return story;
}

function storySectionOf(story) {
  const rep = storyRepresentativeItem(story);
  return rep ? itemSection(rep) : "all";
}

function storyMatchesSection(story, sectionId = state.activeSection) {
  return !sectionId || sectionId === "all" || storySectionOf(story) === sectionId;
}

function storyMatchesQuery(story, query = state.query.trim().toLowerCase()) {
  if (!query) return true;
  const refs = [
    story,
    story.primary_item,
    ...(Array.isArray(story.sources) ? story.sources : []),
  ].filter(Boolean);
  return refs.some((ref) => {
    const hay = `${ref.title || ""} ${ref.title_zh || ""} ${ref.title_en || ""} ${ref.source || ""} ${ref.source_name || ""} ${ref.site_name || ""}`.toLowerCase();
    return hay.includes(query);
  });
}

function storyMatchesSiteFilter(story) {
  if (!state.siteFilter && !state.authorFilter) return true;
  const refs = [
    storyRepresentativeItem(story),
    ...(Array.isArray(story.sources) ? story.sources : []),
  ].filter(Boolean);
  return refs.some((ref) => {
    if (state.siteFilter && ref.site_id !== state.siteFilter) return false;
    if (state.authorFilter && itemXAuthor(ref) !== state.authorFilter) return false;
    return true;
  });
}

function reasonText(item) {
  const creatorScore = creatorHotScore(item);
  if (creatorScore && itemSourceGroup(item) === "creator") {
    const metrics = item.creator_metrics || {};
    const parts = [
      `赞 ${fmtNumber(metrics.likes)}`,
      `藏 ${fmtNumber(metrics.collects)}`,
      `评 ${fmtNumber(metrics.comments)}`,
      `转 ${fmtNumber(metrics.shares)}`,
    ];
    if (Number(item.creator_freshness_bonus || 0) > 0) parts.push("24h 加分");
    return `一周互动：${parts.join(" · ")}`;
  }
  const recommendReason = String(item?.recommend_reason_zh || "").trim();
  if (recommendReason) return recommendReason;
  const signals = Array.isArray(item.ai_signals) ? item.ai_signals.filter(Boolean).slice(0, 3) : [];
  if (signals.length) return `命中方向：${signals.join(" / ")}`;
  if (item.ai_relevance_reason) return String(item.ai_relevance_reason).replaceAll("_", " ");
  return "来源与标题信号通过筛选";
}

function timelineIso(item) {
  const published = item.published_at || "";
  const seen = item.first_seen_at || "";
  const generated = state.generatedAt || "";
  if (published && generated) {
    const publishedMs = new Date(published).getTime();
    const generatedMs = new Date(generated).getTime();
    if (Number.isFinite(publishedMs) && Number.isFinite(generatedMs) && publishedMs > generatedMs + 10 * 60 * 1000) {
      return seen || published;
    }
  }
  return published || seen;
}

function timelineMs(item) {
  const d = new Date(timelineIso(item));
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

function normalizedEventText(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/https?:\/\/\S+/g, "")
    .replace(/[\s ]+/g, "")
    .replace(/[，。、""'：:；;！!？?（）()\[\]【】《》<>·.,/\\|_-]/g, "");
}

function eventKey(item) {
  const raw = itemTitleText(item);
  const bracket = raw.match(/《([^》]{4,40})》/);
  if (bracket) return `book:${normalizedEventText(bracket[1]).slice(0, 36)}`;
  const normalized = normalizedEventText(raw);
  const model = normalized.match(/(bitcpmcann|deepseekv\d+(?:pro)?|grokv\d+(?:medium)?|gemini\d+(?:\.\d+)?(?:flash|pro)?|gpt\d+(?:\.\d+)?|llama\d+)/);
  if (model) return `entity:${model[1]}`;
  return `title:${normalized.slice(0, 34)}`;
}

function itemIdentityKeys(item) {
  const keys = new Set();
  if (!item) return keys;
  const url = item.url || item.primary_url;
  if (url) keys.add(`url:${url}`);
  if (item.id) keys.add(`id:${item.id}`);
  const title = item.title_zh || item.title || item.title_en || item.title_original;
  if (title) {
    keys.add(`event:${eventKey({ ...item, title, title_zh: item.title_zh || title })}`);
    keys.add(`title:${normalizedEventText(title).slice(0, 34)}`);
  }
  return keys;
}

function storyIdentityKeys(story) {
  const keys = new Set();
  if (!story) return keys;
  const refs = [
    { id: story.story_id, title: story.title, url: story.primary_url || story.url },
    story.primary_item,
    ...(Array.isArray(story.sources) ? story.sources : []),
    ...(Array.isArray(story.items) ? story.items : []),
  ].filter(Boolean);
  refs.forEach((ref) => {
    itemIdentityKeys(ref).forEach((key) => keys.add(key));
  });
  return keys;
}

function storyHasAnyKey(story, keys) {
  if (!keys || !keys.size) return false;
  for (const key of storyIdentityKeys(story)) {
    if (keys.has(key)) return true;
  }
  return false;
}

function storyTimeMs(story, key) {
  const iso = story && story[key];
  if (!iso) return 0;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

function storyScore(story) {
  const raw = (story && (story.importance_score ?? story.score ?? story.importance)) || 0;
  const score = Number(raw);
  if (!Number.isFinite(score) || score <= 0) return 0;
  return Math.round(score <= 1 ? score * 100 : score);
}

function storyPrimaryTitleText(story) {
  const primary = (story && story.primary_item) || {};
  const explicit = String(primary.title_enhanced_zh || primary.title_zh || "").trim();
  const explicitOriginal = String(primary.title_en || primary.title_original || "").trim();
  if (explicit) return repairDisplayedTitle(explicitOriginal, explicit);
  const bilingual = String(primary.title || (story && story.title) || "").trim();
  if (bilingual.includes(" / ")) {
    const [zh, en] = bilingual.split(" / ");
    return repairDisplayedTitle(explicitOriginal || en, (zh || en || bilingual).trim());
  }
  return bilingual || "未命名更新";
}

function storyPrimaryEnText(story) {
  const primary = (story && story.primary_item) || {};
  const explicit = String(primary.title_en || primary.title_original || "").trim();
  if (isMostlyEnglishTitle(explicit) && explicit !== storyPrimaryTitleText(story)) return explicit;
  const bilingual = String(primary.title || (story && story.title) || "").trim();
  if (bilingual.includes(" / ")) {
    const [, en] = bilingual.split(" / ");
    const original = (en || "").trim();
    return isMostlyEnglishTitle(original) ? original : "";
  }
  return "";
}

function storySourceCount(story) {
  const sources = Array.isArray(story && story.sources) ? story.sources : [];
  const explicit = Number(story && story.duplicate_count);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  return Math.max(1, sources.length);
}

function dedupedStorySources(row) {
  const story = row && row.story;
  if (!story) return [];
  const sources = Array.isArray(story.sources) ? story.sources : [];
  const primaryUrl = (row.item && row.item.url) || story.primary_url || story.url || "";
  const filtered = primaryUrl ? sources.filter((src) => src && src.url !== primaryUrl) : sources;
  return filtered.length ? filtered : sources;
}

function buildEventSourceRow(source) {
  const row = document.createElement("div");
  row.className = "event-source-row";
  const titleLink = document.createElement("a");
  titleLink.className = "event-source-title";
  titleLink.href = source.url || "#";
  titleLink.target = "_blank";
  titleLink.rel = "noopener noreferrer";
  titleLink.textContent = itemTitleText(source);
  const nameEl = document.createElement("span");
  nameEl.className = "event-source-name";
  nameEl.textContent = source.source_name || source.site_name || source.source || "来源";
  const timeEl = document.createElement("span");
  timeEl.className = "event-source-time";
  timeEl.textContent = fmtRelativeTime(timelineMs(source));
  row.append(titleLink, nameEl, timeEl);
  return row;
}

function buildEventSourceList(row) {
  const sources = dedupedStorySources(row);
  if (!sources.length) return null;
  const list = document.createElement("div");
  list.className = "event-expand-list";
  sources.forEach((source) => list.appendChild(buildEventSourceRow(source)));
  return list;
}

const HOT_DECAY_HOURS = 12;
const HOT_SCORE_SCALE = 60;

function storyHotness(story) {
  const sources = storySourceCount(story);
  if (sources < 2) return 0;
  const latest = storyTimeMs(story, "latest_at") || storyTimeMs(story, "earliest_at");
  const ageHours = latest ? Math.max(0, (Date.now() - latest) / 3600000) : 24;
  return (sources - 1) * Math.exp(-ageHours / HOT_DECAY_HOURS);
}

function storyHotScore(story) {
  const raw = storyHotness(story);
  if (raw <= 0) return 0;
  return Math.max(1, Math.min(100, Math.round(raw * HOT_SCORE_SCALE)));
}

function hotStories(stories) {
  return stories
    .filter((story) => storyHotness(story) > 0)
    .sort((a, b) => {
      const byHotScore = storyHotScore(b) - storyHotScore(a);
      if (byHotScore !== 0) return byHotScore;
      const byHotRaw = storyHotness(b) - storyHotness(a);
      if (byHotRaw !== 0) return byHotRaw;
      const byEditorial = storyScore(b) - storyScore(a);
      if (byEditorial !== 0) return byEditorial;
      return storyTimeMs(b, "latest_at") - storyTimeMs(a, "latest_at");
    });
}

const HOT_BOARD_LIMIT = 20;

function briefStories() {
  return (Array.isArray(state.dailyBrief?.items) ? state.dailyBrief.items : []).filter((story) => !isUnsafeStory(story));
}

function mergedStories() {
  return (Array.isArray(state.storiesMerged?.stories) ? state.storiesMerged.stories : []).filter((story) => !isUnsafeStory(story));
}

let _briefIdentityKeyCache = null;
function briefIdentityKeySet() {
  if (_briefIdentityKeyCache) return _briefIdentityKeyCache;
  const keys = new Set();
  briefStories().forEach((story) => storyIdentityKeys(story).forEach((key) => keys.add(key)));
  _briefIdentityKeyCache = keys;
  return keys;
}

function isStoryCurated(story) {
  return storyHasAnyKey(story, briefIdentityKeySet());
}

function isCuratedSourceRef(ref) {
  if (!ref) return false;
  return ref.site_id === "official_ai" || ref.site_id === "aihot" || ref.source_tier === "official" || ref.source_tier === "curated";
}

function hotBoardStories() {
  return hotStories(mergedStories().filter((story) =>
    storySourceCount(story) >= 2 &&
    storyMatchesSiteFilter(story) &&
    storyMatchesQuery(story)));
}

function hotBoardEntries() {
  if (state.mode !== "selected") return [];
  return hotBoardStories()
    .filter((story) => storyMatchesSection(story))
    .slice(0, HOT_BOARD_LIMIT)
    .map((story, index) => storyToRow(story, index));
}

function mainListStoriesBase() {
  return mergedStories().filter((story) => storyMatchesSiteFilter(story) && storyMatchesQuery(story));
}

function mainListRawItemsBase() {
  const q = state.query.trim().toLowerCase();
  return effectiveAllItems().filter((item) => {
    if (state.siteFilter && item.site_id !== state.siteFilter) return false;
    if (state.authorFilter && itemXAuthor(item) !== state.authorFilter) return false;
    if (!q) return true;
    return itemHaystack(item).includes(q);
  });
}

function mainListStories() {
  return mainListStoriesBase().filter((story) => storyMatchesSection(story));
}

function mainListRawItems() {
  return mainListRawItemsBase().filter((item) => itemMatchesSection(item));
}

function storyToRow(story, index = 0) {
  const enrichStoryItem = (entry) => ({
    ...entry,
    site_name: entry.site_name || entry.source_name || story.source_name || "",
  });
  const item = enrichStoryItem(storyRepresentativeItem(story) || story);
  const sourceItems = [
    item,
    ...(Array.isArray(story.sources) ? story.sources.map(enrichStoryItem) : []),
  ].filter(Boolean);
  const sourceSignals = Array.from(new Set(sourceItems.map(sourceSignal)));
  return {
    item,
    index,
    story,
    rows: sourceItems.map((sourceItem) => ({ item: sourceItem })),
    sourceSignals,
    sourceCount: storySourceCount(story),
    mergedCount: Math.max(1, Number(story.duplicate_count) || sourceItems.length),
    score: storyScore(story),
  };
}

function itemToRow(item, index = 0) {
  return {
    item,
    index,
    story: null,
    rows: [{ item }],
    sourceSignals: [sourceSignal(item)],
    sourceCount: 1,
    mergedCount: 1,
    score: 0,
  };
}

function mainListEntries() {
  if (state.mode === "all") {
    return mainListRawItems().map((item, index) => {
      const ms = timelineMs(item);
      return { row: itemToRow(item, index), timeMs: ms };
    }).sort((a, b) => b.timeMs - a.timeMs);
  }
  return mainListStories().map((story, index) => {
    const ms = storyTimeMs(story, "latest_at") || storyTimeMs(story, "earliest_at");
    return { row: storyToRow(story, index), timeMs: ms };
  }).sort((a, b) => b.timeMs - a.timeMs);
}

function itemSourceRefs(item, row = null) {
  const refs = [];
  const seen = new Set();
  const add = (label, tone) => {
    const clean = String(label || "").trim();
    if (!clean) return;
    const key = `${tone}:${clean}`;
    if (seen.has(key)) return;
    seen.add(key);
    refs.push({ label: clean, tone });
  };
  if (row && Array.isArray(row.sourceSignals) && row.sourceSignals.length) {
    row.sourceSignals.forEach((signal) => add(signal, sourceSignalTone(signal)));
  } else if (row && Array.isArray(row.rows) && row.rows.length) {
    row.rows.forEach((entry) => {
      const sourceItem = entry.item || {};
      const kind = sourceKind(sourceItem.site_id);
      add(sourceItem.source || sourceItem.site_name || kind.label, kind.tone);
    });
  } else {
    const kind = sourceKind(item.site_id);
    add(item.source || item.site_name || kind.label, kind.tone);
  }
  return refs.length ? refs : [{ label: "来源", tone: "default" }];
}

function rowSourceCount(row) {
  const item = row.item || {};
  const refs = itemSourceRefs(item, row);
  const storyCount = row.story ? storySourceCount(row.story) : 0;
  return Math.max(1, refs.length, Number(row.sourceCount || 0), Number(row.mergedCount || 0), storyCount);
}

function signalSummaryText(row) {
  const item = row.item || {};
  const story = row.story || {};
  const editorialSummary = itemSummaryText(item) || itemSummaryText(story.primary_item || {});
  if (editorialSummary) return editorialSummary;
  const reason = reasonText(item);
  if (reason && !reason.startsWith("来源与标题")) return reason.replace(/^命中方向：/, "核心方向：");
  return "";
}

function whyImportantText(row) {
  const item = row.item || {};
  const story = row.story || {};
  const recommendReason = String(
    item.recommend_reason_zh || story.primary_item?.recommend_reason_zh || ""
  ).trim();
  return recommendReason;
}

function feedSummaryText(item) {
  const editorialSummary = itemSummaryText(item);
  if (editorialSummary) return editorialSummary;
  const signals = Array.isArray(item.ai_signals) ? item.ai_signals.filter(Boolean).slice(0, 2) : [];
  if (signals.length) return `相关线索：${signals.join(" / ")}。`;
  const reason = reasonText(item);
  if (reason && !reason.startsWith("来源与标题")) return reason.replace(/^命中方向：/, "相关线索：");
  return "";
}

// ═══════════════════════════════════════════════════════════════
// 渲染函数
// ═══════════════════════════════════════════════════════════════

function updateSidebarNav() {
  document.querySelectorAll(".sb-nav-item[data-section]").forEach((el) => {
    const sectionId = el.dataset.section;
    el.classList.toggle("active", state.activeSection === sectionId);
    const cntEl = cntEls[sectionId];
    if (cntEl) {
      if (sectionId === "all") {
        cntEl.textContent = fmtNumber(state.mode === "all" ? mainListRawItemsBase().length : mainListStoriesBase().length);
      } else if (sectionId === "selected") {
        cntEl.textContent = fmtNumber(mainListEntries().length);
      } else if (sectionId === "hot") {
        cntEl.textContent = fmtNumber(hotBoardEntries().length);
      } else {
        const pool = state.mode === "all" ? mainListRawItemsBase() : mainListStoriesBase();
        const count = sectionId === "all"
          ? pool.length
          : pool.filter(state.mode === "all"
            ? (item) => itemSection(item) === sectionId
            : (story) => storySectionOf(story) === sectionId).length;
        cntEl.textContent = fmtNumber(count);
      }
    }
  });
}

function renderSidebarStatus() {
  const status = state.sourceStatus;
  if (!sbDotEl || !sbSourceStatusEl) return;
  if (!status) {
    sbDotEl.className = "sb-dot warn";
    sbSourceStatusEl.textContent = "源状态加载中";
    return;
  }
  const failed = failedSourceCount(status);
  if (failed > 2) {
    sbDotEl.className = "sb-dot bad";
  } else if (failed > 0) {
    sbDotEl.className = "sb-dot warn";
  } else {
    sbDotEl.className = "sb-dot";
  }
  const totalSites = Array.isArray(status.sites) ? status.sites.length : 0;
  const okSites = Number(status.successful_sites || 0);
  sbSourceStatusEl.textContent = failed
    ? `${fmtNumber(okSites)}/${fmtNumber(totalSites)} 正常`
    : `${fmtNumber(okSites)} 源正常`;
}

function renderDailyBrief() {
  if (!dailyBriefEl) return;
  const brief = state.dailyBrief;
  if (!brief) {
    if (dbTextEl) dbTextEl.textContent = "提莫正在侦察中，稍后为你汇总今日信号…";
    if (dbDateEl) dbDateEl.textContent = "-";
    return;
  }
  const stories = briefStories();
  if (dbDateEl) {
    const dateStr = brief.generated_at ? fmtTime(brief.generated_at) : fmtTime(new Date().toISOString());
    dbDateEl.textContent = dateStr;
  }
  if (dbTextEl) {
    const briefSummary = brief.summary || brief.brief_summary || "";
    if (briefSummary) {
      dbTextEl.textContent = briefSummary;
    } else if (stories.length) {
      const topStories = stories.slice(0, 3).map((s) => storyPrimaryTitleText(s));
      dbTextEl.textContent = `今日重点：${topStories.join("；")}。`;
    } else {
      dbTextEl.textContent = "提莫正在替你盯着所有信源，稍后给你汇报。";
    }
  }
  if (dbStatSignalsEl) dbStatSignalsEl.textContent = fmtNumber(state.totalAi);
  if (dbStatCuratedEl) dbStatCuratedEl.textContent = fmtNumber(stories.filter((s) => isStoryCurated(s)).length);
  if (dbStatSourcesEl) {
    const status = state.sourceStatus;
    const okCount = Number(status?.successful_sites || 0);
    dbStatSourcesEl.textContent = fmtNumber(okCount);
  }
  if (dbStatHotEl) dbStatHotEl.textContent = fmtNumber(hotBoardStories().length);
}

function renderTeemoPanel() {
  if (teemoQuoteEl) {
    const idx = Math.floor(Math.random() * TEEMO_QUOTES.length);
    teemoQuoteEl.textContent = `"${TEEMO_QUOTES[idx]}"`;
  }
  if (teemoStatsEl) {
    teemoStatsEl.innerHTML = "";
    const items = [
      { color: "var(--brand)", text: `今日信号 ${fmtNumber(state.totalAi)} 条` },
      { color: "var(--brand-2)", text: `热点事件 ${fmtNumber(hotBoardStories().length)} 条` },
      { color: "var(--good)", text: `信源正常 ${fmtNumber(Number(state.sourceStatus?.successful_sites || 0))} 个` },
    ];
    items.forEach(({ color, text }) => {
      const div = document.createElement("div");
      div.className = "tp-item";
      div.innerHTML = `<div class="dot" style="background:${color}"></div>${text}`;
      teemoStatsEl.appendChild(div);
    });
  }
}

function renderHealthRing() {
  if (!healthRingEl || !healthPercentEl || !healthLabelEl) return;
  const status = state.sourceStatus;
  if (!status) {
    healthRingEl.style.background = `conic-gradient(var(--line) 0deg)`;
    healthPercentEl.textContent = "-";
    healthLabelEl.textContent = "加载中…";
    return;
  }
  const totalSites = Array.isArray(status.sites) ? status.sites.length : 0;
  const okSites = Number(status.successful_sites || 0);
  const failed = failedSourceCount(status);
  const pct = totalSites ? Math.round((okSites / totalSites) * 100) : 0;
  const deg = Math.round((pct / 100) * 360);
  const color = failed > 2 ? "var(--bad)" : failed > 0 ? "var(--warn)" : "var(--good)";
  healthRingEl.style.background = `conic-gradient(${color} ${deg}deg, var(--line) ${deg}deg)`;
  healthPercentEl.textContent = `${pct}%`;
  healthPercentEl.style.color = color;
  healthLabelEl.textContent = failed
    ? `${fmtNumber(okSites)}/${fmtNumber(totalSites)} 正常 · 失败 ${fmtNumber(failed)}`
    : `${fmtNumber(okSites)} 源全部正常`;

  if (healthItemsEl) {
    healthItemsEl.innerHTML = "";
    const rows = siteRows().slice(0, 5);
    rows.forEach((site) => {
      const div = document.createElement("div");
      div.className = "hp-item";
      const statusClass = site.ok ? "ok" : "bad";
      div.innerHTML = `
        <span class="hp-item-name">${site.site_name || site.site_id}</span>
        <span class="hp-item-status"><span class="hp-dot ${statusClass}"></span>${site.ok ? "正常" : "异常"}</span>
      `;
      healthItemsEl.appendChild(div);
    });
  }
}

function renderActivityGrid() {
  if (!activityGridEl) return;
  activityGridEl.innerHTML = "";
  const stories = mergedStories();
  const now = Date.now();
  const days = 30;
  const dayMs = 86400000;
  const counts = new Array(days).fill(0);
  stories.forEach((story) => {
    const ms = storyTimeMs(story, "latest_at") || storyTimeMs(story, "earliest_at");
    if (!ms) return;
    const daysAgo = Math.floor((now - ms) / dayMs);
    if (daysAgo >= 0 && daysAgo < days) {
      counts[days - 1 - daysAgo] += 1;
    }
  });
  const maxCount = Math.max(1, ...counts);
  counts.forEach((count) => {
    const cell = document.createElement("div");
    cell.className = "ag-c";
    const level = count === 0 ? 0 : Math.ceil((count / maxCount) * 4);
    if (level > 0) cell.classList.add(`l${level}`);
    cell.title = `${count} 条`;
    activityGridEl.appendChild(cell);
  });
}

function renderNewsCards() {
  const entries = mainListEntries().slice(0, 2);
  [newsCard1El, newsCard2El].forEach((cardEl, idx) => {
    if (!cardEl) return;
    const entry = entries[idx];
    if (!entry) {
      cardEl.style.display = "none";
      return;
    }
    cardEl.style.display = "";
    const { row } = entry;
    const item = row.item || {};
    const metaEl = cardEl.querySelector(`#nc${idx + 1}Meta`);
    const titleEl = cardEl.querySelector(`#nc${idx + 1}Title`);
    const descEl = cardEl.querySelector(`#nc${idx + 1}Desc`);
    const tagsEl = cardEl.querySelector(`#nc${idx + 1}Tags`);
    const linkEl = cardEl.querySelector(`#nc${idx + 1}Link`);
    if (metaEl) {
      metaEl.innerHTML = "";
      const srcBadge = document.createElement("span");
      srcBadge.className = "nc-badge src";
      srcBadge.textContent = sourceSignal(item);
      metaEl.appendChild(srcBadge);
      const catBadge = document.createElement("span");
      catBadge.className = "nc-badge cat";
      catBadge.textContent = sectionBadgeLabel(itemSection(item));
      metaEl.appendChild(catBadge);
      if (row.score > 0) {
        const scoreBadge = document.createElement("span");
        scoreBadge.className = "nc-badge score";
        scoreBadge.textContent = `${row.score} 分`;
        metaEl.appendChild(scoreBadge);
      }
    }
    const displayTitle = row.story ? storyPrimaryTitleText(row.story) : itemTitleText(item);
    if (titleEl) {
      titleEl.textContent = displayTitle;
      titleEl.href = item.url || row.story?.primary_url || row.story?.url || "#";
    }
    if (descEl) {
      const summaryText = row.story ? signalSummaryText(row) : feedSummaryText(item);
      descEl.textContent = summaryText || "暂无摘要";
    }
    if (tagsEl) {
      tagsEl.innerHTML = "";
      const tags = Array.isArray(item.ai_signals) ? item.ai_signals.filter(Boolean).slice(0, 3) : [];
      tags.forEach((tag) => {
        const span = document.createElement("span");
        span.className = "nc-badge";
        span.textContent = tag;
        tagsEl.appendChild(span);
      });
    }
    if (linkEl) {
      linkEl.href = item.url || row.story?.primary_url || row.story?.url || "#";
    }
  });
}

function renderHotBoard() {
  if (!hotListEl) return;
  const show = state.mode === "selected";
  if (hotBoardEl) hotBoardEl.style.display = show ? "" : "none";
  if (!show) return;
  hotListEl.innerHTML = "";
  const rows = hotBoardEntries();
  if (!rows.length) {
    hotListEl.innerHTML = '<div class="hl-loading">当前没有多信源交叉的热点</div>';
    return;
  }
  rows.forEach((row, index) => {
    const item = row.item || {};
    const el = document.createElement("div");
    el.className = "hl-item";
    const rankEl = document.createElement("div");
    rankEl.className = "hl-rank";
    if (index === 1) rankEl.classList.add("r2");
    if (index === 2) rankEl.classList.add("r3");
    rankEl.textContent = index + 1;
    const contentEl = document.createElement("div");
    contentEl.className = "hl-content";
    const titleEl = document.createElement("a");
    titleEl.className = "hl-title";
    const displayTitle = row.story ? storyPrimaryTitleText(row.story) : itemTitleText(item);
    titleEl.textContent = displayTitle;
    titleEl.title = displayTitle;
    titleEl.href = item.url || row.story?.primary_url || row.story?.url || "#";
    titleEl.target = "_blank";
    titleEl.rel = "noopener noreferrer";
    const metaEl = document.createElement("div");
    metaEl.className = "hl-meta";
    const sourceCount = rowSourceCount(row);
    const relTime = fmtRelativeTime(timelineMs(item) || storyTimeMs(row.story, "latest_at"));
    metaEl.textContent = `${fmtNumber(sourceCount)} 个信源 · ${relTime}`;
    contentEl.append(titleEl, metaEl);
    el.append(rankEl, contentEl);
    hotListEl.appendChild(el);
  });
  if (hotMoreLinkEl) {
    hotMoreLinkEl.onclick = (e) => {
      e.preventDefault();
      state.activeSection = "hot";
      updateSidebarNav();
      renderMainList();
      fullListWrapEl?.scrollIntoView({ behavior: "smooth" });
    };
  }
}

function renderItemNode(row) {
  const node = itemTpl.content.firstElementChild.cloneNode(true);
  const item = row.item || {};

  const srcEl = node.querySelector("#cardSrc");
  const scoreEl = node.querySelector("#cardScore");
  const catEl = node.querySelector("#cardCat");
  const curatedEl = node.querySelector("#cardCurated");
  const titleEl = node.querySelector("#cardTitle");
  const descEl = node.querySelector("#cardDesc");
  const tagsEl = node.querySelector("#cardTags");
  const linkEl = node.querySelector("#cardLink");

  if (srcEl) {
    srcEl.textContent = sourceSignal(item);
  }

  if (scoreEl) {
    if (row.score > 0) {
      scoreEl.hidden = false;
      scoreEl.textContent = `${row.score} 分`;
    } else {
      scoreEl.hidden = true;
    }
  }

  if (catEl) {
    catEl.textContent = sectionBadgeLabel(itemSection(item));
  }

  const curatedRefs = [item, ...(row.story && Array.isArray(row.story.sources) ? row.story.sources : [])];
  const curated = (row.story && isStoryCurated(row.story)) || curatedRefs.some(isCuratedSourceRef);
  if (curatedEl) curatedEl.hidden = !curated;

  const displayTitle = row.story ? storyPrimaryTitleText(row.story) : itemTitleText(item);
  if (titleEl) {
    titleEl.textContent = displayTitle;
    titleEl.href = item.url || row.story?.primary_url || row.story?.url || "#";
  }

  if (descEl) {
    const summaryText = row.story ? signalSummaryText(row) : feedSummaryText(item);
    descEl.textContent = summaryText || "";
    descEl.hidden = !summaryText;
  }

  if (tagsEl) {
    tagsEl.innerHTML = "";
    const refs = itemSourceRefs(item, row);
    refs.forEach(({ label, tone }) => {
      const span = document.createElement("span");
      span.className = "nc-badge";
      span.textContent = label;
      tagsEl.appendChild(span);
    });
    const signals = Array.isArray(item.ai_signals) ? item.ai_signals.filter(Boolean).slice(0, 2) : [];
    signals.forEach((tag) => {
      const span = document.createElement("span");
      span.className = "nc-badge";
      span.textContent = tag;
      tagsEl.appendChild(span);
    });
  }

  if (linkEl) {
    linkEl.href = item.url || row.story?.primary_url || row.story?.url || "#";
  }

  return node;
}

function scheduleRender(cb) {
  if (document.hidden) {
    setTimeout(cb, 0);
  } else {
    requestAnimationFrame(cb);
  }
}

let _renderListToken = 0;
const MAIN_LIST_PAGE_SIZE = 60;
state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;

function renderMainList() {
  const entries = mainListEntries();
  if (flCountEl) flCountEl.textContent = `${fmtNumber(entries.length)} 条`;
  if (flTitleEl) {
    const section = state.activeSection !== "all" ? SECTION_BY_ID[state.activeSection] : null;
    const label = modeLabelText();
    flTitleEl.textContent = section ? `${section.label} · ${label}` : label;
  }
  if (tbTitleEl) {
    const section = state.activeSection !== "all" ? SECTION_BY_ID[state.activeSection] : null;
    tbTitleEl.textContent = section ? `${section.label} · ${modeLabelText()}` : "提莫侦察中";
  }

  newsListEl.innerHTML = "";
  _renderListToken += 1;
  const token = _renderListToken;

  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.style.cssText = "padding:40px;text-align:center;color:var(--muted);";
    empty.textContent = "没有找到匹配内容";
    newsListEl.appendChild(empty);
    if (loadMoreBtnEl) loadMoreBtnEl.hidden = true;
    return;
  }

  const loading = document.createElement("div");
  loading.style.cssText = "padding:20px;text-align:center;color:var(--muted);font-size:13px;";
  loading.textContent = `正在整理 ${fmtNumber(entries.length)} 条…`;
  newsListEl.appendChild(loading);

  scheduleRender(() => {
    if (token !== _renderListToken) return;
    newsListEl.innerHTML = "";
    const visibleCount = Math.max(MAIN_LIST_PAGE_SIZE, state.mainListVisibleCount || MAIN_LIST_PAGE_SIZE);
    const visible = entries.slice(0, visibleCount);
    const frag = document.createDocumentFragment();
    visible.forEach(({ row }) => {
      frag.appendChild(renderItemNode(row));
    });
    newsListEl.appendChild(frag);
    if (loadMoreBtnEl) {
      if (entries.length > visible.length) {
        loadMoreBtnEl.hidden = false;
        loadMoreBtnEl.textContent = `加载更多 ${fmtNumber(entries.length - visible.length)} 条`;
        loadMoreBtnEl.onclick = () => {
          state.mainListVisibleCount = visibleCount + MAIN_LIST_PAGE_SIZE;
          renderMainList();
        };
      } else {
        loadMoreBtnEl.hidden = true;
      }
    }
    document.dispatchEvent(new CustomEvent("aiRadar:listRendered"));
  });
}

function clearAllFilters() {
  state.query = "";
  state.activeSection = "all";
  state.siteFilter = "";
  state.authorFilter = "";
  state.mode = "selected";
  state.allDedup = true;
  state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;
  state.xAuthorsExpanded = false;
  if (searchInputEl) searchInputEl.value = "";
  rerenderCurrentView();
}

function rerenderCurrentView() {
  state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;
  updateSidebarNav();
  renderModeSwitch();
  renderHotBoard();
  renderNewsCards();
  renderMainList();
  renderDailyBrief();
  renderTeemoPanel();
  renderHealthRing();
  renderActivityGrid();
  renderSidebarStatus();
}

function renderModeSwitch() {
  if (modeSelectedBtnEl) {
    modeSelectedBtnEl.classList.toggle("on", state.mode === "selected");
  }
  if (modeAllBtnEl) {
    modeAllBtnEl.classList.toggle("on", state.mode === "all");
  }
}

// ═══════════════════════════════════════════════════════════════
// 数据加载
// ═══════════════════════════════════════════════════════════════

async function loadNewsData() {
  const res = await fetch(`${dataUrl("data/latest-24h.json")}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 latest-24h.json 失败: ${res.status}`);
  return res.json();
}

async function loadAllModeData() {
  if (state.allDataLoaded) return;
  if (!state.allDataPromise) {
    state.allDataPromise = fetch(`${dataUrl(state.allDataUrl)}?t=${Date.now()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`加载 latest-24h-all.json 失败: ${res.status}`);
        return res.json();
      })
      .then((payload) => {
        state.itemsAllRaw = payload.items_all_raw || payload.items_all || state.itemsAi;
        state.itemsAll = payload.items_all || state.itemsAi;
        state.totalRaw = payload.total_items_raw || state.itemsAllRaw.length;
        state.totalAllMode = payload.total_items_all_mode || state.itemsAll.length;
        state.allDataLoaded = true;
      })
      .catch((err) => {
        state.allDataPromise = null;
        throw err;
      });
  }
  return state.allDataPromise;
}

async function loadSourceStatusData() {
  const res = await fetch(`${dataUrl("data/source-status.json")}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 source-status.json 失败: ${res.status}`);
  return res.json();
}

async function loadDailyBriefData() {
  const res = await fetch(`${dataUrl("data/daily-brief.json")}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 daily-brief.json 失败: ${res.status}`);
  return res.json();
}

async function loadTop3PersonasData() {
  const res = await fetch(`${dataUrl("data/top3-personas.json")}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 top3-personas.json 失败: ${res.status}`);
  return res.json();
}

async function loadStoriesData() {
  const res = await fetch(`${dataUrl(state.storiesDataUrl)}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`加载 stories-merged.json 失败: ${res.status}`);
  return res.json();
}

// ═══════════════════════════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════════════════════════

async function init() {
  const [newsResult, statusResult, briefResult, storiesResult, personasResult] = await Promise.allSettled([
    loadNewsData(),
    loadSourceStatusData(),
    loadDailyBriefData(),
    loadStoriesData(),
    loadTop3PersonasData(),
  ]);

  if (briefResult.status === "fulfilled") {
    state.dailyBrief = briefResult.value;
  } else {
    state.dailyBrief = null;
  }
  _briefIdentityKeyCache = null;

  if (
    personasResult.status === "fulfilled" &&
    Array.isArray(personasResult.value?.items) &&
    personasResult.value.items.length > 0
  ) {
    state.top3Personas = personasResult.value;
  } else {
    state.top3Personas = null;
  }

  if (storiesResult.status === "fulfilled") {
    state.storiesMerged = storiesResult.value;
  } else {
    state.storiesMerged = null;
  }

  if (newsResult.status === "fulfilled") {
    const payload = newsResult.value;
    const loadedStoriesDataUrl = state.storiesDataUrl;
    state.itemsAi = payload.items_ai || payload.items || [];
    state.itemsAllRaw = payload.items_all_raw || payload.items_all || [];
    state.itemsAll = payload.items_all || [];
    state.creatorItemsAi = payload.creator_items_ai || [];
    state.creatorItemsAll = payload.creator_items_all || state.creatorItemsAi;
    state.creatorWindowDays = Number(payload.creator_window_days || 7);
    state.statsAi = payload.site_stats || [];
    state.totalAi = payload.total_items || state.itemsAi.length;
    state.totalRaw = payload.total_items_raw || state.itemsAllRaw.length;
    state.totalAllMode = payload.total_items_all_mode || state.itemsAll.length;
    state.allDataUrl = payload.all_mode_data_url || state.allDataUrl;
    state.storiesDataUrl = payload.stories_data_url || state.storiesDataUrl;
    if (state.storiesDataUrl !== loadedStoriesDataUrl) {
      try {
        state.storiesMerged = await loadStoriesData();
      } catch {
        state.storiesMerged = null;
      }
    }
    state.allDataLoaded = Boolean(payload.items_all || payload.items_all_raw);
    state.generatedAt = payload.generated_at;

    if (sbUpdatedAtEl) sbUpdatedAtEl.textContent = fmtTime(state.generatedAt);
    if (tbTimeEl) tbTimeEl.textContent = fmtTime(state.generatedAt);
  } else {
    if (sbUpdatedAtEl) sbUpdatedAtEl.textContent = "数据加载失败";
    if (newsListEl) newsListEl.innerHTML = `<div style="padding:40px;text-align:center;color:var(--muted);">${newsResult.reason.message}</div>`;
  }

  if (statusResult.status === "fulfilled") {
    state.sourceStatus = statusResult.value;
  }

  updateSidebarNav();
  renderModeSwitch();
  renderHotBoard();
  renderNewsCards();
  renderMainList();
  renderDailyBrief();
  renderTeemoPanel();
  renderHealthRing();
  renderActivityGrid();
  renderSidebarStatus();

  document.dispatchEvent(new CustomEvent("aiRadar:ready"));
}

// ═══════════════════════════════════════════════════════════════
// 事件绑定
// ═══════════════════════════════════════════════════════════════

searchInputEl?.addEventListener("input", (e) => {
  state.query = e.target.value;
  state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;
  updateSidebarNav();
  renderHotBoard();
  renderNewsCards();
  renderMainList();
});

document.querySelectorAll(".sb-nav-item[data-section]").forEach((el) => {
  el.addEventListener("click", () => {
    const sectionId = el.dataset.section;
    if (sectionId === "hot") {
      state.activeSection = "all";
    } else {
      state.activeSection = sectionId;
    }
    state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;
    updateSidebarNav();
    renderHotBoard();
    renderNewsCards();
    renderMainList();
    closeSidebar();
  });
});

modeSelectedBtnEl?.addEventListener("click", () => {
  if (state.mode === "selected") return;
  state.mode = "selected";
  state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;
  rerenderCurrentView();
});

modeAllBtnEl?.addEventListener("click", async () => {
  if (state.mode === "all") return;
  state.mode = "all";
  state.mainListVisibleCount = MAIN_LIST_PAGE_SIZE;
  renderModeSwitch();
  newsListEl.innerHTML = "";
  const loading = document.createElement("div");
  loading.style.cssText = "padding:20px;text-align:center;color:var(--muted);";
  loading.textContent = "正在加载全量更新...";
  newsListEl.appendChild(loading);
  try {
    await loadAllModeData();
    rerenderCurrentView();
  } catch (err) {
    newsListEl.innerHTML = "";
    const failed = document.createElement("div");
    failed.style.cssText = "padding:20px;text-align:center;color:var(--muted);";
    failed.textContent = err.message;
    newsListEl.appendChild(failed);
  }
});

function openSidebar() {
  sidebarEl?.classList.add("open");
  sidebarOverlayEl?.classList.add("open");
}

function closeSidebar() {
  sidebarEl?.classList.remove("open");
  sidebarOverlayEl?.classList.remove("open");
}

menuBtnEl?.addEventListener("click", () => {
  const isOpen = sidebarEl?.classList.contains("open");
  if (isOpen) {
    closeSidebar();
  } else {
    openSidebar();
  }
});

sidebarOverlayEl?.addEventListener("click", closeSidebar);

init();
