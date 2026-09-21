# Radar 自动发现与统一翻译流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `ai-news-radar` 中实现一个可插拔、可增量、可观测的自动发现与内容生产平台，统一处理抓取、清洗、版本、翻译、质量校验和 Export Bundle。

**Architecture:** Radar 从 Way-to-agentic 的 Source/Framework/Surface registry 自动发现需要执行的模块。每个模块通过 Connector 生成原始文档，经 Normalize 和 Identity 层形成 Revision，再由统一 `TranslationRouter` 优先调用 DeepSeek、失败后回退 Google，最终由 Quality Gate 和 Export Builder 发布版本化内容包。

**Tech Stack:** Python 3.11、SQLite、JSON、`hashlib`、`sqlite3`、`zoneinfo`、现有 `requests`、`feedparser`、`beautifulsoup4`、DeepSeek Chat Completions、Google Translate、GitHub Actions。

**Spec:** `../../../../../way-to-agentic/.worktrees/radar-protocol/docs/架构设计.md`

## Global Constraints

- Radar 只消费 Way registry，不写入 Way registry。
- Radar 的运行时状态统一进入 SQLite；Export Bundle 只发布可消费结果和摘要。
- 所有内容身份优先使用 `source_native_id`、`canonical_url`，标题不能作为唯一 ID。
- 翻译幂等键必须包含 `content_id`、`revision_id`、`target_locale`、`translation_profile`、`policy_version`。
- DeepSeek 是首选翻译 provider；Google 是 fallback，不允许在业务脚本中直接调用任一 provider。
- 代码块、URL、CLI、API 签名、标识符和占位符必须由 Markdown 层保护。
- Provider 错误、无效响应、拒答、过短译文和结构损坏都必须进入可重试或人工复核状态。
- 不使用 DeepSeek 生成稳定 ID、hash、cursor 或任务依赖。
- 每个任务必须可重跑，重复执行不能产生重复有效 artifact。
- 不改动用户现有未提交文件；新增计划和实现必须保持独立。

## 文件结构

**Create:**

- `radar_core/__init__.py`
- `radar_core/config.py`
- `radar_core/contracts.py`
- `radar_core/ids.py`
- `radar_core/hashing.py`
- `radar_core/storage.py`
- `radar_core/registry.py`
- `radar_core/discovery.py`
- `radar_core/normalize.py`
- `radar_core/dedupe.py`
- `radar_core/pipeline.py`
- `radar_core/export.py`
- `radar_core/report.py`
- `radar_core/connectors/base.py`
- `radar_core/connectors/rss_article.py`
- `radar_core/connectors/llms_txt.py`
- `radar_core/connectors/github_tree.py`
- `radar_core/connectors/deepwiki.py`
- `radar_core/connectors/local_import.py`
- `radar_core/translation/base.py`
- `radar_core/translation/deepseek.py`
- `radar_core/translation/google.py`
- `radar_core/translation/router.py`
- `radar_core/translation/markdown.py`
- `radar_core/quality.py`
- `scripts/radar_run.py`
- `scripts/radar_export.py`
- `tests/test_contracts.py`
- `tests/test_ids_and_hashes.py`
- `tests/test_state_store.py`
- `tests/test_registry_discovery.py`
- `tests/test_connectors.py`
- `tests/test_normalize_and_dedupe.py`
- `tests/test_translation_router.py`
- `tests/test_markdown_translation.py`
- `tests/test_quality_gate.py`
- `tests/test_pipeline.py`
- `tests/test_export_bundle.py`
- `tests/fixtures/way-registry/`
- `tests/fixtures/source-documents/`

**Modify:**

- `requirements.txt`
- `.github/workflows/update-news.yml`
- `scripts/update_news.py`
- `scripts/write_job_report.py`
- `scripts/write_export_manifest.py`

**Delete after cutover:**

- Direct provider calls in `scripts/update_news.py`.
- Direct full-text translation path in `scripts/sync_knowledge_sources.py`.
- Direct Markdown translation path in `scripts/translate_registered_markdown.py`.
- Legacy execution branches in `.github/workflows/update-news.yml`.

### Task 1: Freeze the runtime contract loader

**Files:**

- Create: `radar_core/contracts.py`
- Create: `radar_core/config.py`
- Create: `tests/test_contracts.py`
- Create: `tests/fixtures/way-registry/protocol.json`
- Create: `tests/fixtures/way-registry/registry/index.json`
- Modify: `requirements.txt` only if the chosen schema validator is added; default implementation uses stdlib validation

**Interfaces:**

- `load_protocol(target_root: Path) -> dict[str, Any]`
- `load_registry_document(target_root: Path) -> dict[str, Any]`
- `validate_contract_document(document: dict[str, Any], schema_name: str) -> list[str]`
- `RuntimeConfig.from_env(env: Mapping[str, str]) -> RuntimeConfig`

- [ ] **Step 1: Write failing tests**

  Test that the loader rejects a missing protocol, an unsupported contract
  version, duplicate IDs, unsafe paths, malformed cron expressions, and a
  task without an adapter or output declaration.

- [ ] **Step 2: Run the focused test**

  ```bash
  python -m pytest tests/test_contracts.py -q
  ```

  Expected: FAIL because `radar_core` does not exist.

- [ ] **Step 3: Implement the contract loader**

  Load the Way checkout passed as `target_root`. Validate only declaration
  structure here; do not execute connectors or touch content files. Return
  normalized dictionaries with `source_id`, `entity_id`, `surface_id`,
  `schedule`, `translation_profile`, and `output`.

- [ ] **Step 4: Run the focused test**

  ```bash
  python -m pytest tests/test_contracts.py -q
  ```

  Expected: PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add radar_core tests/test_contracts.py tests/fixtures/way-registry requirements.txt
  git commit -m "feat: add Radar content contract loader"
  ```

### Task 2: Add deterministic identity, hashing, and SQLite ledgers

**Files:**

- Create: `radar_core/ids.py`
- Create: `radar_core/hashing.py`
- Create: `radar_core/storage.py`
- Create: `tests/test_ids_and_hashes.py`
- Create: `tests/test_state_store.py`

**Interfaces:**

- `canonicalize_url(url: str) -> str`
- `content_id_for(source_id: str, native_id: str | None, canonical_url: str | None, slug: str | None) -> str`
- `revision_id_for(normalized_text: str, normalizer_version: str) -> str`
- `translation_key(content_id: str, revision_id: str, locale: str, profile: str, policy: str) -> str`
- `StateStore.open(path: Path) -> StateStore`
- `StateStore.get_or_create_item(content_id: str, payload: dict[str, Any]) -> dict[str, Any]`
- `StateStore.record_revision(payload: dict[str, Any]) -> dict[str, Any]`
- `StateStore.get_translation(key: str) -> dict[str, Any] | None`
- `StateStore.upsert_translation(payload: dict[str, Any]) -> None`
- `StateStore.advance_cursor(source_id: str, cursor: dict[str, Any], run_id: str) -> None`

- [ ] **Step 1: Write failing tests**

  Cover URL normalization, native ID precedence, stable fallback slug,
  content hash changes, translation key changes when policy changes, SQLite
  uniqueness, and cursor advancement only after an explicit success call.

- [ ] **Step 2: Run tests and verify failure**

  ```bash
  python -m pytest tests/test_ids_and_hashes.py tests/test_state_store.py -q
  ```

- [ ] **Step 3: Implement the schema**

  Use `sqlite3` with these tables:

  ```text
  sources
  cursors
  content_items
  revisions
  artifacts
  translations
  runs
  task_runs
  ```

  Add unique constraints on `content_id`, `revision_id`, and the full
  translation idempotency key. Store hashes and status separately from paths.

- [ ] **Step 4: Run tests and verify pass**

  ```bash
  python -m pytest tests/test_ids_and_hashes.py tests/test_state_store.py -q
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add radar_core/ids.py radar_core/hashing.py radar_core/storage.py tests/test_ids_and_hashes.py tests/test_state_store.py
  git commit -m "feat: add Radar identity and revision ledgers"
  ```

### Task 3: Implement registry-driven automatic discovery

**Files:**

- Create: `radar_core/registry.py`
- Create: `radar_core/discovery.py`
- Create: `tests/test_registry_discovery.py`
- Modify: `scripts/radar_registry.py` only during the cutover adapter, not in the new core path

**Interfaces:**

- `load_sources(target_root: Path) -> list[SourceSpec]`
- `load_framework_entities(target_root: Path) -> list[EntitySpec]`
- `load_surfaces(target_root: Path) -> list[SurfaceSpec]`
- `discover_due_operations(registry: Registry, now: datetime, state: StateStore) -> list[Operation]`
- `register_new_sources(registry: Registry, state: StateStore, run_id: str) -> list[str]`

- [ ] **Step 1: Write failing tests**

  Use a fixture with one existing source and one newly added source. Assert
  that the new source is discovered without changing Radar Python code, gets
  a cursor row, and produces a due operation according to its timezone and
  cron.

- [ ] **Step 2: Run the focused test**

  ```bash
  python -m pytest tests/test_registry_discovery.py -q
  ```

- [ ] **Step 3: Implement registry discovery**

  Read the target checkout's new `sources/`, `entities/`, and `surfaces/`
  manifests. Create state rows lazily. Treat a disabled source as visible but
  not executable. Keep the registry's schedule as the only schedule source.

- [ ] **Step 4: Add discovery diagnostics**

  Return `added_sources`, `changed_sources`, `disabled_sources`, invalid
  declarations, and next run times. Never silently ignore a new manifest.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_registry_discovery.py -q
  git add radar_core/registry.py radar_core/discovery.py tests/test_registry_discovery.py
  git commit -m "feat: discover Radar operations from Way registry"
  ```

### Task 4: Add Connector SDK and first-party adapters

**Files:**

- Create: `radar_core/connectors/base.py`
- Create: `radar_core/connectors/rss_article.py`
- Create: `radar_core/connectors/llms_txt.py`
- Create: `radar_core/connectors/github_tree.py`
- Create: `radar_core/connectors/deepwiki.py`
- Create: `radar_core/connectors/local_import.py`
- Create: `tests/test_connectors.py`
- Create: `tests/fixtures/source-documents/`

**Interfaces:**

- `Connector.discover(cursor: Cursor) -> DiscoveryPage`
- `Connector.fetch(item: DiscoveredItem) -> RawDocument`
- `Connector.healthcheck() -> HealthStatus`
- `ConnectorFactory.create(adapter: str, config: dict[str, Any]) -> Connector`

- [ ] **Step 1: Write failing fixture tests**

  Use local RSS XML, llms.txt, GitHub tree JSON, DeepWiki response text, and
  local import files. Assert that each adapter returns the same
  `DiscoveredItem` and `RawDocument` shape.

- [ ] **Step 2: Run tests and verify failure**

  ```bash
  python -m pytest tests/test_connectors.py -q
  ```

- [ ] **Step 3: Implement the Connector interface**

  Connectors may perform HTTP and parsing only. They must not write SQLite,
  call translation providers, or write Way output. Return provider cursors,
  etags, last-modified values, and stable native IDs when available.

- [ ] **Step 4: Add safe HTTP behavior**

  Use timeouts, retry-after handling, bounded response sizes, content-type
  checks, and redacted error messages. Cache conditional request headers in
  the cursor.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_connectors.py -q
  git add radar_core/connectors tests/test_connectors.py tests/fixtures/source-documents
  git commit -m "feat: add pluggable Radar connectors"
  ```

### Task 5: Add normalization, identity matching, and duplicate handling

**Files:**

- Create: `radar_core/normalize.py`
- Create: `radar_core/dedupe.py`
- Create: `tests/test_normalize_and_dedupe.py`

**Interfaces:**

- `normalize_document(raw: RawDocument, profile: str) -> NormalizedDocument`
- `normalized_hash(document: NormalizedDocument) -> str`
- `exact_match(document: NormalizedDocument, state: StateStore) -> Match | None`
- `duplicate_candidates(document: NormalizedDocument, state: StateStore) -> list[Match]`
- `accept_revision(document: NormalizedDocument, state: StateStore) -> Revision`

- [ ] **Step 1: Write failing tests**

  Assert that dynamic CDN query parameters and fetch timestamps do not create
  a new revision, while a changed paragraph does. Assert that a source-native
  ID wins over URL and that a same-body different-URL item is marked as a
  reusable-content candidate, not silently merged.

- [ ] **Step 2: Run focused tests**

  ```bash
  python -m pytest tests/test_normalize_and_dedupe.py -q
  ```

- [ ] **Step 3: Implement Markdown/HTML normalization**

  Preserve headings, lists, tables, links, code fences, images, and source
  metadata. Remove navigation, scripts, cookie banners, dynamic build IDs,
  and unstable tracking parameters according to a versioned normalizer profile.

- [ ] **Step 4: Implement exact and candidate matching**

  Exact identity can reuse an item. Same normalized hash can reuse a
  translation artifact. Semantic similarity only creates
  `possible_duplicate`; it never deletes or merges a content item.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_normalize_and_dedupe.py -q
  git add radar_core/normalize.py radar_core/dedupe.py tests/test_normalize_and_dedupe.py
  git commit -m "feat: normalize and deduplicate Radar content"
  ```

### Task 6: Introduce the unified TranslationRouter

**Files:**

- Create: `radar_core/translation/base.py`
- Create: `radar_core/translation/deepseek.py`
- Create: `radar_core/translation/google.py`
- Create: `radar_core/translation/router.py`
- Create: `tests/test_translation_router.py`

**Interfaces:**

- `TranslationProvider.translate(request: TranslationRequest) -> TranslationResponse`
- `DeepSeekProvider.translate(request: TranslationRequest) -> TranslationResponse`
- `GoogleProvider.translate(request: TranslationRequest) -> TranslationResponse`
- `TranslationRouter.translate(request: TranslationRequest) -> TranslationResponse`
- `TranslationRouter.provider_order() -> tuple[str, ...]`

- [ ] **Step 1: Write failing provider tests**

  Mock DeepSeek success, missing key, timeout, HTTP 429, malformed JSON,
  refusal text, and invalid output. Assert that valid DeepSeek output wins and
  every invalid/unavailable case falls back to Google.

- [ ] **Step 2: Run the focused test**

  ```bash
  python -m pytest tests/test_translation_router.py -q
  ```

- [ ] **Step 3: Implement provider adapters**

  DeepSeek configuration:

  ```text
  DEEPSEEK_API_KEY
  DEEPSEEK_API_BASE_URL
  DEEPSEEK_MODEL
  ```

  Google configuration uses the existing public translation endpoint and a
  bounded request timeout. Provider errors must be typed as retryable,
  permanent, or invalid-output.

- [ ] **Step 4: Implement router and telemetry**

  The router tries DeepSeek first, then Google. Record provider, model,
  prompt version, policy version, input hash, output hash, latency, attempt,
  and fallback reason. Never log source body or secret values.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_translation_router.py -q
  git add radar_core/translation tests/test_translation_router.py
  git commit -m "feat: add DeepSeek-first translation router"
  ```

### Task 7: Add Markdown segmentation and translation quality gates

**Files:**

- Create: `radar_core/translation/markdown.py`
- Create: `radar_core/quality.py`
- Create: `tests/test_markdown_translation.py`
- Create: `tests/test_quality_gate.py`
- Modify: `translation-glossary.txt` only when a reviewed term is required

**Interfaces:**

- `split_markdown(text: str) -> list[MarkdownUnit]`
- `translate_markdown(document: str, request: TranslationRequest, router: TranslationRouter) -> TranslationArtifact`
- `validate_structure(source: str, translated: str) -> list[QualityIssue]`
- `validate_terminology(source: str, translated: str, glossary: Glossary) -> list[QualityIssue]`
- `quality_gate(source: str, translated: str, profile: str) -> QualityReport`

- [ ] **Step 1: Write failing tests**

  Cover code fences, inline code, URLs, tables, Mermaid, placeholders,
  headings, repeated segments, provider fallback, empty output, refusal
  output, and a translated paragraph that changes only one source segment.

- [ ] **Step 2: Run focused tests**

  ```bash
  python -m pytest tests/test_markdown_translation.py tests/test_quality_gate.py -q
  ```

- [ ] **Step 3: Implement stable segmentation**

  Create deterministic units for prose blocks and protect non-translatable
  spans with opaque placeholders. Segment cache keys must include the source
  segment hash, locale, profile, glossary version, and model policy version.

- [ ] **Step 4: Implement quality gate**

  Reject missing headings, changed code fences, broken URLs, lost placeholders,
  refusal text, very short translations, malformed tables, and non-UTF-8
  output. Return `machine_passed`, `needs_review`, or `rejected`.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_markdown_translation.py tests/test_quality_gate.py -q
  git add radar_core/translation/markdown.py radar_core/quality.py tests/test_markdown_translation.py tests/test_quality_gate.py
  git commit -m "feat: add incremental Markdown translation and quality gates"
  ```

### Task 8: Build the end-to-end pipeline and idempotent task runner

**Files:**

- Create: `radar_core/pipeline.py`
- Create: `tests/test_pipeline.py`
- Modify: `scripts/run_registered_tasks.py` only as a temporary invocation shim

**Interfaces:**

- `run_source(source: SourceSpec, context: RunContext) -> SourceRunResult`
- `run_operation(operation: Operation, context: RunContext) -> OperationResult`
- `should_translate(revision: Revision, request: TranslationRequest, state: StateStore) -> bool`
- `advance_cursor_after_success(source_id: str, cursor: Cursor, context: RunContext) -> None`

- [ ] **Step 1: Write failing integration tests**

  Use a fake connector and fake providers. Run the same source twice and
  assert the second run produces zero new translation calls. Change one source
  paragraph and assert exactly one new revision and one new translation.

- [ ] **Step 2: Run the focused test**

  ```bash
  python -m pytest tests/test_pipeline.py -q
  ```

- [ ] **Step 3: Implement the operation graph**

  Execute:

  ```text
  discover → fetch → normalize → identity → revision
  → translate → validate → persist artifact → report
  ```

  Advance the connector cursor only after all items in the page are persisted
  or explicitly recorded as retryable failures.

- [ ] **Step 4: Add retries and leases**

  Use SQLite leases keyed by source/task. Retry provider and network failures
  with exponential backoff. Do not retry invalid source data indefinitely.
  A crashed lease becomes reclaimable after its expiry.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_pipeline.py -q
  git add radar_core/pipeline.py tests/test_pipeline.py scripts/run_registered_tasks.py
  git commit -m "feat: run Radar sources idempotently"
  ```

### Task 9: Generate versioned Export Bundles

**Files:**

- Create: `radar_core/export.py`
- Create: `radar_core/report.py`
- Create: `scripts/radar_export.py`
- Create: `tests/test_export_bundle.py`
- Create: `tests/fixtures/expected-export/`
- Modify: `scripts/write_job_report.py`
- Modify: `scripts/write_export_manifest.py`

**Interfaces:**

- `build_export_bundle(run_id: str, state: StateStore, out_dir: Path) -> ExportManifest`
- `verify_export_bundle(bundle_root: Path) -> list[str]`
- `write_run_report(run: RunRecord, out: Path) -> None`
- `build_legacy_free_report(run: RunRecord) -> dict[str, Any]`

- [ ] **Step 1: Write failing bundle tests**

  Assert that a bundle contains `manifest.json`, `sources.jsonl`,
  `items.jsonl`, `revisions.jsonl`, `translations.jsonl`, artifacts,
  `checksums.txt`, and `run-report.json`. Assert that checksum failure,
  missing artifact, incomplete manifest, and unsafe path are rejected.

- [ ] **Step 2: Run focused tests**

  ```bash
  python -m pytest tests/test_export_bundle.py -q
  ```

- [ ] **Step 3: Implement bundle creation**

  Use this output layout:

  ```text
  export/<export_id>/
    manifest.json
    sources.jsonl
    items.jsonl
    revisions.jsonl
    translations.jsonl
    artifacts/<sha256>.bin
    checksums.txt
    run-report.json
  ```

  The manifest schema is `radar-content-export/v1`; all paths are relative
  and all artifacts are content-addressed.

- [ ] **Step 4: Implement verification and report**

  Report discovered, fetched, normalized, reused, translated, fallback,
  quality-failed, retrying, and published counts. Include source/task IDs and
  next run times, but never include full external bodies.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_export_bundle.py -q
  git add radar_core/export.py radar_core/report.py scripts/radar_export.py scripts/write_job_report.py scripts/write_export_manifest.py tests/test_export_bundle.py tests/fixtures/expected-export
  git commit -m "feat: publish verified Radar content bundles"
  ```

### Task 10: Add the CLI and GitHub Actions execution path

**Files:**

- Create: `scripts/radar_run.py`
- Modify: `.github/workflows/update-news.yml`
- Modify: `requirements.txt` only if connector implementation requires a new pinned dependency
- Create: `tests/test_radar_cli.py`

**Interfaces:**

- `python scripts/radar_run.py --target-root <way-checkout> --state <sqlite> --report <run-report>`
- `python scripts/radar_export.py --state <sqlite> --run-id <id> --out <bundle>`

- [ ] **Step 1: Write failing CLI tests**

  Test dry-run, one-source execution, provider selection, state path,
  report path, export path, and non-zero exit on incomplete export.

- [ ] **Step 2: Run focused tests**

  ```bash
  python -m pytest tests/test_radar_cli.py -q
  ```

- [ ] **Step 3: Implement CLI commands**

  `radar_run.py` loads Way registry, registers new sources, executes due
  operations, writes the run report, and leaves the cursor unchanged on
  fatal failure. `--dry-run` performs discovery and validation without
  network writes or translation calls.

- [ ] **Step 4: Replace the workflow execution block**

  Keep the existing source collection and snapshot generation only until the
  new pipeline has passed the end-to-end fixture. Then change the workflow to:

  ```text
  checkout Way registry
  → install Radar
  → run radar_run.py
  → build export bundle
  → publish bundle and run report
  ```

  Configure `DEEPSEEK_API_KEY`, `DEEPSEEK_API_BASE_URL`, `DEEPSEEK_MODEL`,
  `GITEE_TOKEN`, `GITEE_REPO`, and the state/export paths through Actions
  secrets or variables.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_radar_cli.py -q
  git add scripts/radar_run.py .github/workflows/update-news.yml tests/test_radar_cli.py requirements.txt
  git commit -m "feat: run Radar automation from one workflow"
  ```

### Task 11: Cut over existing sources and remove scattered translation paths

**Files:**

- Modify: `scripts/update_news.py`
- Modify: `scripts/sync_knowledge_sources.py`
- Modify: `scripts/translate_registered_markdown.py`
- Modify: `.github/workflows/update-news.yml`
- Create: `tests/test_no_scattered_translation.py`

**Interfaces:**

- All content translation calls go through `radar_core.translation.router.TranslationRouter`.
- Existing news, knowledge, docs, and Wiki entrypoints become either thin
  adapters or are removed after the workflow cutover.

- [ ] **Step 1: Write failing architecture tests**

  Assert that the production pipeline imports `TranslationRouter`, that no
  production path directly calls `translate.googleapis.com` or
  `api.deepseek.com`, and that the workflow has one translation entrypoint.

- [ ] **Step 2: Run focused tests**

  ```bash
  python -m pytest tests/test_no_scattered_translation.py -q
  ```

- [ ] **Step 3: Route all translation through the router**

  Remove direct provider functions from `update_news.py`,
  `sync_knowledge_sources.py`, and `translate_registered_markdown.py`.
  Preserve only source-specific parsing and output projection where needed.

- [ ] **Step 4: Disable legacy branches**

  Remove the legacy workflow branch that calls
  `sync_knowledge_sources.py` directly. Delete the old `markdown_google`
  adapter from the new registry execution path.

- [ ] **Step 5: Run tests and commit**

  ```bash
  python -m pytest tests/test_no_scattered_translation.py tests/test_translation_router.py tests/test_pipeline.py -q
  git add scripts/update_news.py scripts/sync_knowledge_sources.py scripts/translate_registered_markdown.py .github/workflows/update-news.yml tests/test_no_scattered_translation.py
  git commit -m "refactor: centralize all Radar translation"
  ```

### Task 12: Run production-like fixture and release checklist

**Files:**

- Create: `tests/test_end_to_end_export.py`
- Create: `docs/radar-runtime.md`
- Create: `docs/radar-operations.md`
- Modify: `.github/workflows/update-news.yml` for dry-run/manual inputs

**Interfaces:**

- `run_fixture_pipeline(target_root, state_path, export_root) -> dict[str, Any]`

- [ ] **Step 1: Write the end-to-end failing test**

  Register one RSS source, one DeepWiki surface, one local import, and one
  framework. Execute discovery, fake fetch, DeepSeek success, DeepSeek
  failure with Google fallback, export, checksum verification, and a repeated
  run.

- [ ] **Step 2: Run the end-to-end test**

  ```bash
  python -m pytest tests/test_end_to_end_export.py -q
  ```

- [ ] **Step 3: Implement the missing wiring**

  Ensure the fixture proves that new manifests are auto-discovered, unchanged
  revisions are skipped, changed revisions are translated, failed providers
  are reported, and the bundle is complete.

- [ ] **Step 4: Document operations**

  Document environment variables, state database backup, cursor reset,
  provider fallback behavior, retry states, dry-run, export verification, and
  rollback. Include exact commands for local execution and GitHub Actions.

- [ ] **Step 5: Run the full Radar suite**

  ```bash
  python -m pytest -q
  git diff --check
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add tests/test_end_to_end_export.py docs/radar-runtime.md docs/radar-operations.md .github/workflows/update-news.yml
  git commit -m "test: verify end-to-end Radar automation"
  ```

## Final Radar Acceptance Checklist

- [ ] Adding a Source manifest causes Radar to discover it on the next run.
- [ ] Adding a Framework surface does not require Python code changes.
- [ ] DeepSeek is attempted before Google for every eligible translation.
- [ ] Google fallback is recorded with a reason.
- [ ] Same revision does not invoke a provider twice.
- [ ] Changed source content creates a new revision.
- [ ] Translation policy changes create a new translation key.
- [ ] Failed runs do not advance source cursors.
- [ ] Export Bundle is complete, checksummed, and independently verifiable.
- [ ] The run report contains per-source and per-task summaries.
- [ ] No production script directly owns a translation provider.
