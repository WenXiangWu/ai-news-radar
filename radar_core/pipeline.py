from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import os
import time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from .connectors.base import (
    Connector,
    ConnectorError,
    ConnectorFactory,
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    merge_nested_source_config,
)
from .dedupe import Revision, accept_revision
from .discovery import Operation
from .ledger import select_items
from .normalize import NormalizedDocument, normalize_document
from .registry import SourceSpec
from .storage import StateStore
from .translation.base import (
    DEFAULT_POLICY_VERSION,
    TranslationRequest,
    TranslationResponse,
    output_hash,
)
from .translation.markdown import TranslationArtifact, translate_markdown
from .translation.router import TranslationRouter


ConnectorBuilder = Callable[[SourceSpec], Connector]


RunMode = Literal["incremental", "baseline_only", "bootstrap"]


@dataclass
class RunContext:
    state: StateStore
    run_id: str
    target_locales: tuple[str, ...] = ("zh-CN",)
    connector_factory: ConnectorBuilder | None = None
    router: Any = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_at: datetime | None = None
    policy_version: str = DEFAULT_POLICY_VERSION
    dry_run: bool = False
    glossary: Mapping[str, str] | None = None
    mode: RunMode = "incremental"
    skip_translation: bool = False
    max_new_items_override: int | None = None

    def __post_init__(self) -> None:
        self.target_locales = tuple(self.target_locales or ("zh-CN",))
        if self.router is None:
            self.router = TranslationRouter()


@dataclass
class SourceRunResult:
    source_id: str
    status: str = "running"
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int = 0
    discovered: int = 0
    selected: int = 0
    deferred: int = 0
    skipped_not_modified: int = 0
    run_kind: str = "incremental"
    fetched: int = 0
    normalized: int = 0
    revisions_new: int = 0
    revisions_reused: int = 0
    translated: int = 0
    translation_reused: int = 0
    quality_failed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    provider_counts: dict[str, int] = field(default_factory=dict)
    provider_failures: dict[str, int] = field(default_factory=dict)
    provider_fallbacks: dict[str, int] = field(default_factory=dict)
    cursor: dict[str, Any] | None = None
    updated_content_ids: list[str] = field(default_factory=list)
    translated_content_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"success", "partial"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "ok": self.ok,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "discovered": self.discovered,
            "selected": self.selected,
            "deferred": self.deferred,
            "skipped_not_modified": self.skipped_not_modified,
            "run_kind": self.run_kind,
            "fetched": self.fetched,
            "normalized": self.normalized,
            "revisions_new": self.revisions_new,
            "revisions_reused": self.revisions_reused,
            "translated": self.translated,
            "translation_reused": self.translation_reused,
            "quality_failed": self.quality_failed,
            "failed": self.failed,
            "errors": list(self.errors),
            "provider_counts": dict(self.provider_counts),
            "provider_failures": dict(self.provider_failures),
            "provider_fallbacks": dict(self.provider_fallbacks),
            "cursor": dict(self.cursor or {}),
            "updated_content_ids": list(self.updated_content_ids),
            "translated_content_ids": list(self.translated_content_ids),
        }


@dataclass
class OperationResult(SourceRunResult):
    entity_ids: tuple[str, ...] = ()
    surface_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "entity_ids": list(self.entity_ids),
                "surface_ids": list(self.surface_ids),
            }
        )
        return payload


def should_translate(
    revision: Revision,
    request: TranslationRequest,
    state: StateStore,
) -> bool:
    key = _translation_key_for(request)
    existing = state.get_translation(key)
    if existing is None:
        return True
    return str(existing.get("status") or "") in {
        "rejected",
        "needs_review",
        "retrying",
    }


def advance_cursor_after_success(
    source_id: str,
    cursor: Cursor,
    context: RunContext,
) -> None:
    payload = cursor.to_dict()
    if context.scheduled_at is not None:
        payload["last_scheduled_at"] = context.scheduled_at.isoformat()
    context.state.advance_cursor(source_id, payload, context.run_id)


def run_source(source: SourceSpec, context: RunContext) -> SourceRunResult:
    started_wall = datetime.now(timezone.utc)
    started_clock = time.perf_counter()
    result = SourceRunResult(
        source_id=source.id,
        started_at=started_wall.isoformat(),
    )
    context.state.record_run(context.run_id, {"status": "running"})
    if context.dry_run:
        result.status = "dry_run"
        _finish_result(result, started_clock)
        return result
    if context.mode == "bootstrap":
        result.status = "failed"
        result.failed = 1
        result.run_kind = "bootstrap"
        result.errors.append("bootstrap is disabled")
        context.state.record_run(
            context.run_id,
            {"status": "failed", "source_id": source.id, "errors": result.errors},
        )
        _finish_result(result, started_clock)
        return result

    try:
        connector = _connector_for(source, context)
        cursor_row = context.state.get_cursor(source.id)
        cursor = Cursor.coerce(
            cursor_row.get("cursor") if isinstance(cursor_row, dict) else None
        )
        page = connector.discover(cursor)
        result.discovered = len(page.items)
    except Exception as exc:  # noqa: BLE001
        result.status = "failed"
        result.failed = 1
        result.errors.append(_safe_error(exc))
        context.state.record_run(
            context.run_id,
            {"status": "failed", "source_id": source.id, "errors": result.errors},
        )
        _finish_result(result, started_clock)
        return result

    _record_source_snapshot(source, page, context)
    ledger_rows = _upsert_ledger_items(source, page, context)

    result.run_kind = "incremental" if context.mode == "incremental" else context.mode

    # Spec §3.2: `baseline_only` always discovers + writes ledger, fetch=0,
    # translate=0 — even on an already-initialized source. `bootstrap` skips the
    # baseline gate entirely. The implicit first-cron baseline_only path only
    # triggers when the source has not been initialized yet.
    baseline_initialized = context.state.baseline_initialized(source.id)
    if context.mode == "baseline_only" or (
        context.mode != "bootstrap" and not baseline_initialized
    ):
        context.state.mark_source_baseline(source.id, context.run_id)
        result.run_kind = "baseline_only"
        result.status = "success"
        result.cursor = page.cursor.to_dict()
        context.state.record_run(
            context.run_id,
            {"status": "success", "source_id": source.id, "result": result.to_dict()},
        )
        advance_cursor_after_success(source.id, page.cursor, context)
        _commit_source_registration(source, context)
        _finish_result(result, started_clock)
        return result

    if not _connector_supports_incremental(connector, page.items):
        result.status = "unsupported_incremental"
        result.failed = 1
        result.errors.append(
            f"{source.id}: connector cannot supply stable item revisions"
        )
        context.state.record_run(
            context.run_id,
            {
                "status": "unsupported_incremental",
                "source_id": source.id,
                "result": result.to_dict(),
                "errors": result.errors,
            },
        )
        _finish_result(result, started_clock)
        return result

    max_new_items = _max_new_items(
        source,
        override=context.max_new_items_override,
    )
    selected_rows, deferred_rows = select_items(ledger_rows, max_new_items=max_new_items)
    result.selected = len(selected_rows)
    result.deferred = len(deferred_rows)
    result.skipped_not_modified = max(
        0, len(ledger_rows) - len(selected_rows) - len(deferred_rows)
    )

    items_by_id = {item.native_id: item for item in page.items}
    pending = list(selected_rows)
    deferred_queue = list(deferred_rows)

    while pending:
        row = pending.pop(0)
        item = items_by_id.get(row["item_id"])
        if item is None:
            # Spec §3.1: after a 304 the discovery page may be empty, but
            # lagged items (fetched_revision != remote_revision) must still
            # be selected from the ledger. Reconstruct a DiscoveredItem from
            # the ledger row unless the item has been marked missing.
            if str(row.get("status") or "") == "missing":
                continue
            item = _reconstruct_item_from_row(source, row)
            if item is None:
                continue
        try:
            raw = connector.fetch(item)
            result.fetched += 1
            document = normalize_document(raw, source.translation_profile)
            result.normalized += 1
            revision = accept_revision(document, context.state)
            if revision.is_new:
                result.revisions_new += 1
                if revision.content_id not in result.updated_content_ids:
                    result.updated_content_ids.append(revision.content_id)
            else:
                result.revisions_reused += 1
            for locale in context.target_locales:
                translation_ok = _translate_revision(
                    source,
                    document,
                    revision,
                    locale,
                    context,
                    result,
                )
                if not translation_ok:
                    result.failed += 1
                    result.errors.append(
                        f"{item.native_id or item.url}: translation unavailable or failed quality gate"
                    )
                elif revision.content_id not in result.translated_content_ids:
                    result.translated_content_ids.append(revision.content_id)
            _mark_item_fetched(source, item, context)
        except Exception as exc:  # noqa: BLE001
            unfetchable = _unfetchable_status(exc)
            if unfetchable:
                context.state.upsert_source_item(
                    {
                        "source_id": source.id,
                        "item_id": row["item_id"],
                        "canonical_url": row.get("canonical_url") or item.url,
                        "remote_revision": row.get("remote_revision"),
                        "remote_etag": row.get("remote_etag"),
                        "remote_last_modified": row.get("remote_last_modified"),
                        "status": unfetchable,
                    }
                )
                if deferred_queue and result.fetched < max_new_items:
                    pending.append(deferred_queue.pop(0))
                    result.selected += 1
                    result.deferred = max(0, result.deferred - 1)
                continue
            result.failed += 1
            result.errors.append(
                f"{item.native_id or item.url}: {_safe_error(exc)}"
            )

    for row in deferred_rows:
        context.state.upsert_source_item(
            {
                "source_id": source.id,
                "item_id": row["item_id"],
                "canonical_url": row["canonical_url"],
                "remote_revision": row.get("remote_revision"),
                "remote_etag": row.get("remote_etag"),
                "remote_last_modified": row.get("remote_last_modified"),
                "status": "deferred",
            }
        )

    if result.failed:
        result.status = "partial" if result.fetched else "failed"
        context.state.record_run(
            context.run_id,
            {
                "status": "failed",
                "source_id": source.id,
                "errors": result.errors,
                "result": result.to_dict(),
            },
        )
        _finish_result(result, started_clock)
        return result

    result.status = "success"
    result.cursor = page.cursor.to_dict()
    context.state.record_run(
        context.run_id,
        {"status": "success", "source_id": source.id, "result": result.to_dict()},
    )
    advance_cursor_after_success(source.id, page.cursor, context)
    _commit_source_registration(source, context)
    _finish_result(result, started_clock)
    return result


def _record_source_snapshot(
    source: SourceSpec,
    page: DiscoveryPage,
    context: RunContext,
) -> None:
    cursor = page.cursor
    snapshot_id = f"{source.id}:{context.run_id}"
    context.state.record_source_snapshot(
        source.id,
        {
            "snapshot_id": snapshot_id,
            "fetched_at": context.now.isoformat(),
            "remote_manifest_id": cursor.token,
            "etag": cursor.etag,
            "last_modified": cursor.last_modified,
            "item_count": len(page.items),
            "status": "ok",
            "payload": {"has_more": page.has_more},
        },
    )


def _upsert_ledger_items(
    source: SourceSpec,
    page: DiscoveryPage,
    context: RunContext,
) -> list[dict[str, Any]]:
    now = context.now.isoformat()
    # Spec §3.1/§4: discover upsert only updates remote_* / last_seen_at and
    # must NOT overwrite an existing `fetched`/`deferred`/`blocked` status with
    # `seen`. We omit `status` so `upsert_source_item` preserves the prior row
    # status for items that already exist, and defaults new items to `seen`.
    not_modified = bool(page.metadata.get("not_modified"))
    discovered_ids: set[str] = set()
    for item in page.items:
        discovered_ids.add(item.native_id)
        row: dict[str, Any] = {
            "source_id": source.id,
            "item_id": item.native_id,
            "canonical_url": item.canonical_url,
            "remote_revision": item.remote_revision,
            "remote_etag": item.remote_etag,
            "remote_last_modified": item.remote_last_modified,
            "last_seen_at": now,
            # Persist enough metadata to reconstruct a fetchable DiscoveredItem
            # later (e.g. after a 304 manifest). See _reconstruct_item_from_row.
            "title": item.title,
            "published_at": item.published_at,
            "content_type": item.content_type,
            "payload": dict(item.metadata),
        }
        context.state.upsert_source_item(row)
    # When the manifest actually changed (non-304) and re-emitted a non-empty
    # item set, mark ledger items that are no longer present in the discovered
    # page as `missing` so they stop being selected for fetch but are not
    # silently deleted (Spec §4). An empty page (e.g. a 304 without the flag,
    # or a connector that does not re-emit) is treated conservatively and
    # never marks items missing.
    if not not_modified and page.items:
        prior_rows = context.state.list_source_items(source.id)
        for row in prior_rows:
            if row["item_id"] in discovered_ids:
                continue
            if str(row.get("status") or "") in {"missing"}:
                continue
            context.state.upsert_source_item(
                {
                    "source_id": source.id,
                    "item_id": row["item_id"],
                    "canonical_url": row.get("canonical_url") or "",
                    "remote_revision": row.get("remote_revision"),
                    "remote_etag": row.get("remote_etag"),
                    "remote_last_modified": row.get("remote_last_modified"),
                    "status": "missing",
                }
            )
    return context.state.list_source_items(source.id)


def _mark_item_fetched(
    source: SourceSpec,
    item: DiscoveredItem,
    context: RunContext,
) -> None:
    context.state.upsert_source_item(
        {
            "source_id": source.id,
            "item_id": item.native_id,
            "canonical_url": item.canonical_url,
            "remote_revision": item.remote_revision,
            "remote_etag": item.remote_etag,
            "remote_last_modified": item.remote_last_modified,
            "fetched_revision": item.remote_revision,
            "last_fetched_at": context.now.isoformat(),
            "status": "fetched",
        }
    )


def _reconstruct_item_from_row(
    source: SourceSpec,
    row: dict[str, Any],
) -> DiscoveredItem | None:
    """Rebuild a DiscoveredItem from a ledger row when the discovery page
    did not re-emit it (e.g. a 304 manifest). Used to fetch lagged items
    whose fetched_revision still trails remote_revision.

    The ledger row carries the persisted title, published_at, content_type
    and connector metadata (path/url/etc.) so the reconstructed item can be
    fetched without the discovery page being re-emitted.
    """

    item_id = str(row.get("item_id") or "").strip()
    canonical_url = str(row.get("canonical_url") or "").strip()
    if not item_id or not canonical_url:
        return None
    metadata = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    metadata = dict(metadata)
    title = str(row.get("title") or metadata.get("title") or item_id)
    return DiscoveredItem(
        source_id=source.id,
        native_id=item_id,
        url=canonical_url,
        title=title,
        published_at=row.get("published_at") or metadata.get("published_at"),
        content_type=str(
            row.get("content_type") or metadata.get("content_type") or "text/plain"
        ),
        remote_revision=row.get("remote_revision"),
        remote_etag=row.get("remote_etag"),
        remote_last_modified=row.get("remote_last_modified"),
        metadata=metadata,
    )


def _connector_supports_incremental(
    connector: Any,
    items: list[DiscoveredItem],
) -> bool:
    incremental_class = getattr(connector, "incremental_class", None)
    if incremental_class == "unsupported":
        return False
    if incremental_class == "revision-native":
        return True
    if not items:
        return True
    return any(item.has_remote_validator for item in items)


DEFAULT_MAX_NEW_ITEMS = 50


def _max_new_items(source: SourceSpec, override: int | None = None) -> int:
    """Spec §3.5: `max_new_items` is a hard cap applied after selection.
    An *unset* (missing/None) value falls back to a positive default (50)
    so a normal schedule still selects changed items. An *explicit* 0 means
    the task does not fetch new bodies (index task) — changed items are
    deferred, not selected."""
    if override is not None:
        try:
            return max(0, int(override))
        except (TypeError, ValueError):
            pass
    schedule = source.schedule if isinstance(source.schedule, Mapping) else {}
    if "max_new_items" not in schedule or schedule.get("max_new_items") is None:
        return DEFAULT_MAX_NEW_ITEMS
    raw = schedule.get("max_new_items")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_NEW_ITEMS
    return max(0, value)


def _finish_result(result: SourceRunResult, started_clock: float) -> None:
    result.finished_at = datetime.now(timezone.utc).isoformat()
    result.duration_ms = max(0, int((time.perf_counter() - started_clock) * 1000))


def run_operation(operation: Operation, context: RunContext) -> OperationResult:
    operation_context = replace(
        context,
        scheduled_at=operation.scheduled_at,
    )
    result = run_source(operation.source, operation_context)
    return OperationResult(
        **result.__dict__,
        entity_ids=operation.entity_ids,
        surface_ids=operation.surface_ids,
    )


def _translate_revision(
    source: SourceSpec,
    document: NormalizedDocument,
    revision: Revision,
    locale: str,
    context: RunContext,
    result: SourceRunResult,
) -> bool:
    if context.skip_translation:
        return True
    request = TranslationRequest(
        text=document.body,
        source_locale="auto",
        target_locale=locale,
        translation_profile=source.translation_profile,
        content_id=revision.content_id,
        revision_id=revision.revision_id,
        policy_version=context.policy_version,
        metadata={"source_id": source.id},
    )
    if not should_translate(revision, request, context.state):
        result.translation_reused += 1
        return True

    artifact = translate_markdown(document.body, request, context.router)
    response = _last_response(artifact)
    provider = response.provider if response else None
    if provider:
        result.provider_counts[provider] = result.provider_counts.get(provider, 0) + 1
    for translation_response in artifact.responses:
        if translation_response.fallback_reason and translation_response.provider:
            result.provider_fallbacks[translation_response.provider] = (
                result.provider_fallbacks.get(translation_response.provider, 0) + 1
            )
        for telemetry in translation_response.telemetry:
            if telemetry.reason:
                result.provider_failures[telemetry.provider] = (
                    result.provider_failures.get(telemetry.provider, 0) + 1
                )
    status = "machine_passed" if artifact.quality.passed else "needs_review"
    if not artifact.translated_text.strip():
        status = "rejected"
    if not artifact.quality.passed:
        result.quality_failed += 1
    if artifact.responses:
        result.translated += 1
    context.state.upsert_translation(
        {
            "content_id": revision.content_id,
            "revision_id": revision.revision_id,
            "target_locale": locale,
            "translation_profile": source.translation_profile,
            "policy_version": context.policy_version,
            "provider": provider,
            "source_hash": revision.source_hash,
            "output_hash": output_hash(artifact.translated_text),
            "status": status,
            "translated_text": artifact.translated_text,
            "quality": {
                "passed": artifact.quality.passed,
                "profile": artifact.quality.profile,
                "issues": [
                    {
                        "code": issue.code,
                        "message": issue.message,
                        "severity": issue.severity,
                    }
                    for issue in artifact.quality.issues
                ],
            },
            "provider_metadata": dict(response.metadata) if response else {},
        }
    )
    _record_artifact(
        context.state,
        revision,
        locale,
        source.translation_profile,
        context.policy_version,
        artifact,
    )
    return bool(artifact.quality.passed and artifact.translated_text.strip())


def _record_artifact(
    state: StateStore,
    revision: Revision,
    locale: str,
    profile: str,
    policy_version: str,
    artifact: TranslationArtifact,
) -> None:
    if not hasattr(state, "record_artifact"):
        return
    state.record_artifact(
        {
            "artifact_id": _translation_key_for(
                TranslationRequest(
                    text="artifact",
                    target_locale=locale,
                    translation_profile=profile,
                    content_id=revision.content_id,
                    revision_id=revision.revision_id,
                    policy_version=policy_version,
                )
            ),
            "content_id": revision.content_id,
            "revision_id": revision.revision_id,
            "kind": "translation",
            "locale": locale,
            "artifact_hash": output_hash(artifact.translated_text),
            "status": "published" if artifact.quality.passed else "needs_review",
            "path": None,
            "payload": {
                "translated_text": artifact.translated_text,
                "quality_passed": artifact.quality.passed,
            },
        }
    )


def _connector_for(source: SourceSpec, context: RunContext) -> Connector:
    if context.connector_factory is not None:
        return context.connector_factory(source)
    adapter = _adapter_for(source)
    return ConnectorFactory.create(adapter, _connector_config(source, adapter))


def _adapter_for(source: SourceSpec) -> str:
    source_type_adapters = {
        "official_blog": "rss_article",
        "rss": "rss_article",
        "llms_txt": "llms_txt",
        "github": "github_tree",
        "deepwiki": "deepwiki",
        "local": "local_import",
    }
    raw = source.payload
    adapter = str(
        raw.get("adapter")
        or raw.get("connector")
        or ""
    ).strip()
    if adapter:
        return adapter
    source_type = str(raw.get("source_type") or "").strip()
    if source_type in source_type_adapters:
        return source_type_adapters[source_type]
    locator = source.locator.lower()
    if locator.endswith((".xml", ".rss")) or "/feed" in locator:
        return "rss_article"
    if locator.endswith("llms.txt"):
        return "llms_txt"
    if "deepwiki.com" in locator:
        return "deepwiki"
    return "knowledge_source"


def _connector_config(source: SourceSpec, adapter: str) -> dict[str, Any]:
    config = merge_nested_source_config(source.payload)
    config.update(
        {
            "source_id": source.id,
            "adapter_name": adapter,
            "locator": source.locator,
        }
    )
    token = str(
        config.get("token")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or ""
    ).strip()
    if token:
        config["token"] = token
    owner = str(config.get("github_owner") or "").strip()
    repo_name = str(config.get("github_repo") or "").strip()
    if owner and repo_name:
        config.setdefault("repo", f"{owner}/{repo_name}")
    if not str(config.get("ref") or "").strip():
        branch = str(config.get("github_branch") or config.get("branch") or "").strip()
        if branch:
            config["ref"] = branch
    if not str(config.get("path_prefix") or "").strip():
        prefix = str(config.get("github_docs_prefix") or "").strip()
        if prefix:
            config["path_prefix"] = prefix
    if adapter in {"rss", "rss_article"}:
        config.setdefault("feed_url", source.locator)
    elif adapter in {"llms_txt", "deepwiki"}:
        config.setdefault("url", source.locator)
    elif adapter in {"html_collection", "static_pages"}:
        config.setdefault("listing_url", source.locator)
        config.setdefault("url", source.locator)
    elif adapter == "github_tree":
        config.setdefault("repo", source.locator)
    return config


def _translation_key_for(request: TranslationRequest) -> str:
    from .ids import translation_key

    if not request.content_id or not request.revision_id:
        raise ValueError("translation request requires content and revision IDs")
    return translation_key(
        request.content_id,
        request.revision_id,
        request.target_locale,
        request.translation_profile,
        request.policy_version,
    )


def _last_response(artifact: TranslationArtifact) -> TranslationResponse | None:
    return artifact.responses[-1] if artifact.responses else None


def _commit_source_registration(source: SourceSpec, context: RunContext) -> None:
    registration = context.state.get_source_registration(source.id)
    if not registration or registration.get("staged_run_id") != context.run_id:
        return
    try:
        context.state.commit_source_registration(source.id, context.run_id)
    except ValueError:
        return


def _safe_error(error: Exception) -> str:
    message = str(error).strip()
    return message[:500] or type(error).__name__


def _unfetchable_status(error: Exception) -> str | None:
    if not isinstance(error, ConnectorError):
        return None
    message = str(error)
    if "HTTP 404" in message or "HTTP 410" in message:
        return "missing"
    if "response size exceeds" in message:
        return "blocked"
    return None


__all__ = [
    "OperationResult",
    "RunContext",
    "SourceRunResult",
    "advance_cursor_after_success",
    "run_operation",
    "run_source",
    "should_translate",
]
