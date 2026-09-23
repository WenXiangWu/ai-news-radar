from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .report import build_contract_run_report, build_legacy_free_report, write_contract_run_report
from .storage import StateStore


EXPORT_SCHEMA = "radar-content-export/v1"


def build_export_bundle(
    run_id: str,
    state: StateStore,
    out_dir: Path,
) -> dict[str, Any]:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)

    rows_by_table = {
        table: state.iter_rows(table)
        for table in ("sources", "content_items", "revisions", "translations", "artifacts")
    }
    revision_rows = _contract_revisions(rows_by_table["revisions"])
    item_rows = _contract_items(
        rows_by_table["content_items"],
        revision_rows,
    )
    _write_jsonl(root / "sources.jsonl", rows_by_table["sources"])
    _write_jsonl(root / "items.jsonl", item_rows)

    artifact_paths: list[str] = []
    artifact_records: list[dict[str, Any]] = []
    raw_artifact_by_revision: dict[str, dict[str, Any]] = {}
    for revision in revision_rows:
        raw_text = str(revision.pop("_body", "") or "")
        data = raw_text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        local_relative = f"artifacts/raw-{digest}.bin"
        target = root / local_relative
        if not target.exists():
            target.write_bytes(data)
        declared_path = _bundle_path(
            root,
            _export_id(run_id),
            local_relative,
        )
        raw_artifact = {
            "artifact_id": f"artifact.raw.{digest[:32]}",
            "kind": "raw",
            "content_id": revision["content_id"],
            "revision_id": revision["id"],
            "path": declared_path,
            "checksum": f"sha256:{digest}",
            "media_type": "text/plain; charset=utf-8",
        }
        raw_artifact_by_revision[revision["id"]] = raw_artifact
        artifact_paths.append(local_relative)
        artifact_records.append(raw_artifact)
        revision["raw_path"] = declared_path
    _write_jsonl(root / "revisions.jsonl", revision_rows)

    translation_rows = _contract_translations(
        rows_by_table["translations"],
        artifact_records,
        artifact_paths,
        root,
        _export_id(run_id),
    )
    _write_jsonl(root / "translations.jsonl", translation_rows)
    translation_lookup = {
        (
            str(row["content_id"]),
            str(row["revision_id"]),
            str(row["language"]),
        ): str(row["id"])
        for row in translation_rows
    }
    for row in rows_by_table["artifacts"]:
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        value = payload.get("translated_text")
        if value is None:
            value = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        data = str(value).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        relative = f"artifacts/{digest}.bin"
        target = root / relative
        if not target.exists():
            target.write_bytes(data)
        artifact_paths.append(relative)
        content_id = _contract_id(str(row.get("content_id") or ""), "content")
        revision_id = _contract_id(str(row.get("revision_id") or ""), "revision")
        translation_id = translation_lookup.get(
            (
                content_id,
                revision_id,
                str(row.get("locale") or "zh-CN"),
            )
        )
        artifact_record = {
            "artifact_id": f"artifact.translation.{digest[:32]}",
            "kind": "translation" if translation_id else "normalized",
            "content_id": content_id,
            "revision_id": revision_id,
            "path": _bundle_path(root, _export_id(run_id), relative),
            "checksum": f"sha256:{digest}",
            "media_type": "text/markdown; charset=utf-8",
        }
        if translation_id:
            artifact_record["translation_id"] = translation_id
        artifact_records.append(artifact_record)

    run = _find_run(state, run_id)
    report = build_contract_run_report(run)
    write_contract_run_report(run, root / "run-report.json")

    export_id = _export_id(run_id)
    item_ids = [str(row["id"]) for row in item_rows]
    revision_ids = [str(row["id"]) for row in revision_rows]
    translation_ids = [str(row["id"]) for row in translation_rows]
    policy_version = str(
        next(
            (
                row.get("policy_version")
                for row in translation_rows
                if row.get("policy_version")
            ),
            "translation/policy-v1",
        )
    )
    manifest = {
        "schema": EXPORT_SCHEMA,
        "export_id": f"export-{export_id}",
        "protocol_version": "v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": item_ids,
        "revisions": revision_ids,
        "translations": translation_ids,
        "paths": ["frontend/radar-content/current"],
        "bundle_root": f"exports/export-{export_id}",
        "content_root": "frontend/radar-content/current",
        "bundle_owner": "radar",
        "content_owner": "way-to-agentic",
        "source_registry_revision": run_id,
        "policy_version": policy_version,
        "complete": _export_is_complete(run, translation_rows),
        "checksums_path": f"exports/export-{export_id}/checksums.txt",
        "artifacts": artifact_records,
    }
    _write_json(root / "manifest.json", manifest)
    _write_checksums(
        root,
        [
            "manifest.json",
            "sources.jsonl",
            "items.jsonl",
            "revisions.jsonl",
            "translations.jsonl",
            "run-report.json",
            *sorted(set(artifact_paths)),
        ],
    )
    return manifest


def verify_export_bundle(bundle_root: Path) -> list[str]:
    root = Path(bundle_root)
    errors: list[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["manifest.json is invalid"]
    if not isinstance(manifest, Mapping):
        return ["manifest.json must be an object"]
    if manifest.get("schema") != EXPORT_SCHEMA:
        errors.append("manifest schema is invalid")
    if manifest.get("complete") is not True:
        errors.append("manifest complete must be true")

    bundle_root = str(manifest.get("bundle_root") or "")
    if not _is_safe_relative(bundle_root):
        errors.append(f"unsafe bundle root: {bundle_root}")
    standard_files = [
        "sources.jsonl",
        "items.jsonl",
        "revisions.jsonl",
        "translations.jsonl",
        "run-report.json",
    ]
    safe_files = list(standard_files)
    for relative in safe_files:
        if not (root / relative).is_file():
            errors.append(f"missing export file: {relative}")

    checksums_path = root / "checksums.txt"
    checksum_entries = _read_checksums(checksums_path, errors)
    expected_checksum_paths = {
        "manifest.json",
        *safe_files,
        *(
            _local_artifact_path(root, bundle_root, str(item.get("path") or ""))
            for item in manifest.get("artifacts") or []
            if isinstance(item, Mapping)
        ),
    }
    if set(checksum_entries) != expected_checksum_paths:
        errors.append("checksums are incomplete")
    for relative, expected in checksum_entries.items():
        target = root / relative
        if not target.is_file():
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"checksum mismatch: {relative}")

    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            errors.append("artifact record is invalid")
            continue
        relative = str(artifact.get("path") or "")
        local_relative = _local_artifact_path(root, bundle_root, relative)
        if not local_relative:
            errors.append(f"unsafe artifact path: {relative}")
            continue
        target = root / local_relative
        if not target.is_file():
            errors.append(f"missing artifact: {relative}")
            continue
        expected = str(artifact.get("checksum") or "")
        if expected.startswith("sha256:"):
            expected = expected[7:]
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if expected != actual:
            errors.append(f"artifact checksum mismatch: {relative}")
    return errors


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            )


def _write_checksums(root: Path, files: Iterable[str]) -> None:
    entries: list[str] = []
    for relative in sorted(set(files)):
        target = root / relative
        if target.is_file():
            entries.append(
                f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {relative}"
            )
    (root / "checksums.txt").write_text(
        "\n".join(entries) + ("\n" if entries else ""),
        encoding="utf-8",
    )


def _read_checksums(path: Path, errors: list[str]) -> dict[str, str]:
    if not path.is_file():
        errors.append("checksums.txt is missing")
        return {}
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        errors.append("checksums.txt is unreadable")
        return {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append("checksums.txt contains an invalid entry")
            continue
        entries[parts[1]] = parts[0]
    return entries


def _find_run(state: StateStore, run_id: str) -> dict[str, Any]:
    for row in state.iter_rows("runs"):
        if str(row.get("run_id") or "") == run_id:
            payload = row.get("payload")
            result = dict(payload) if isinstance(payload, Mapping) else {}
            result.setdefault("run_id", run_id)
            result.setdefault("status", row.get("status"))
            result.setdefault("updated_at", row.get("updated_at"))
            return result
    return {"run_id": run_id, "status": "unknown"}


def _export_id(run_id: str) -> str:
    return hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:16]


def _contract_id(value: str, prefix: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith(prefix + "."):
        return raw
    suffix = "".join(
        character if character.isalnum() else "."
        for character in raw.lower()
    ).strip(".")
    return f"{prefix}.{suffix or 'unknown'}"


def _contract_revisions(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    revisions: list[dict[str, Any]] = []
    ordered_rows = sorted(
        rows,
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("revision_id") or ""),
        ),
    )
    for row in ordered_rows:
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        content_id = _contract_id(str(row.get("content_id") or ""), "content")
        revision_id = _contract_id(str(row.get("revision_id") or ""), "revision")
        source_hash = str(row.get("source_hash") or "")
        body = str(payload.get("body") or "")
        source_url = str(payload.get("canonical_url") or "https://radar.invalid/content")
        revisions.append(
            {
                "schema": "radar-content-contract/v1/revision",
                "id": revision_id,
                "kind": "revision",
                "content_id": content_id,
                "source_revision": source_hash or revision_id,
                "content_hash": f"sha256:{_hash_value(source_hash or body)}",
                "source_url": source_url,
                "fetched_at": str(row.get("created_at") or datetime.now(timezone.utc).isoformat()),
                "raw_path": "",
                "_body": body,
            }
        )
    return revisions


def _contract_items(
    rows: list[Mapping[str, Any]],
    revisions: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    revision_by_content = {
        str(row["content_id"]): row for row in revisions
    }
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        content_id = _contract_id(str(row.get("content_id") or ""), "content")
        revision = revision_by_content.get(content_id)
        items.append(
            {
                "schema": "radar-content-contract/v1/content-item",
                "id": content_id,
                "kind": "content_item",
                "source_id": str(row.get("source_id") or "source.radar"),
                "title": str(payload.get("title") or content_id),
                "content_type": str(payload.get("content_type") or "documentation"),
                "canonical_url": str(
                    payload.get("canonical_url") or "https://radar.invalid/content"
                ),
                "revision_id": str(
                    revision.get("id") if revision else "revision.unknown"
                ),
            }
        )
    return items


def _contract_translations(
    rows: list[Mapping[str, Any]],
    artifact_records: list[dict[str, Any]],
    artifact_paths: list[str],
    root: Path,
    export_id: str,
) -> list[dict[str, Any]]:
    translations: list[dict[str, Any]] = []
    for row in rows:
        content_id = _contract_id(str(row.get("content_id") or ""), "content")
        revision_id = _contract_id(str(row.get("revision_id") or ""), "revision")
        translation_id = _contract_id(
            str(row.get("translation_key") or ""),
            "translation",
        )
        status = str(row.get("status") or "untranslated")
        status = {
            "machine_passed": "translated",
            "published": "translated",
            "new": "untranslated",
            "rejected": "failed",
        }.get(status, status)
        if status not in {"translated", "untranslated", "failed", "needs_review"}:
            status = "needs_review"
        provider = str(row.get("provider") or "untranslated")
        if provider not in {"deepseek", "google", "manual", "untranslated"}:
            provider = "manual"
        digest = _hash_value(
            str(
                (
                    row.get("payload", {})
                    if isinstance(row.get("payload"), Mapping)
                    else {}
                ).get("translated_text")
                or ""
            )
        )
        local_relative = f"artifacts/{digest}.bin"
        declared_path = _bundle_path(root, export_id, local_relative)
        translations.append(
            {
                "schema": "radar-content-contract/v1/translation",
                "id": translation_id,
                "kind": "translation",
                "content_id": content_id,
                "revision_id": revision_id,
                "language": str(row.get("target_locale") or "zh-CN"),
                "provider": provider,
                "status": status,
                "translated_at": str(
                    row.get("updated_at")
                    or datetime.now(timezone.utc).isoformat()
                ),
                "path": f"frontend/radar-content/current/{digest}.md",
                "source_hash": f"sha256:{_hash_value(str(row.get('source_hash') or ''))}",
                "translation_profile": str(
                    row.get("translation_profile") or "prose/v1"
                ),
                "policy_version": str(
                    row.get("policy_version") or "translation/policy-v1"
                ),
            }
        )
    return translations


def _hash_value(value: str) -> str:
    raw = str(value or "")
    if len(raw) == 64 and all(character in "0123456789abcdef" for character in raw.lower()):
        return raw.lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _export_is_complete(
    run: Mapping[str, Any],
    translations: list[Mapping[str, Any]],
) -> bool:
    status = str(run.get("status") or "").strip().lower()
    if status not in {"success", "ok"}:
        return False
    operations = run.get("operations") or run.get("tasks") or []
    if isinstance(operations, list):
        if any(
            isinstance(operation, Mapping)
            and str(operation.get("status") or "").lower()
            in {"failed", "partial", "blocked"}
            for operation in operations
        ):
            return False
    return all(
        str(row.get("status") or "") == "translated"
        for row in translations
    )


def _bundle_path(root: Path, export_id: str, local_relative: str) -> str:
    return f"exports/export-{export_id}/{local_relative}"


def _is_safe_relative(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _local_artifact_path(root: Path, bundle_root: str, declared: str) -> str:
    if not _is_safe_relative(bundle_root) or not _is_safe_relative(declared):
        return ""
    prefix = bundle_root.rstrip("/") + "/"
    if not declared.startswith(prefix):
        return ""
    return declared[len(prefix):]


def export_successful_subset(report: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Keep only successful operations. Failed source ids stay out of the bundle."""
    operations = [
        row for row in report.get("operations") or []
        if isinstance(row, Mapping)
    ]
    included = [
        dict(row) for row in operations
        if str(row.get("status") or "") == "success"
    ]
    excluded = [
        str(row.get("source_id"))
        for row in operations
        if str(row.get("status") or "") != "success" and row.get("source_id")
    ]
    bundle = {
        "schema": EXPORT_SCHEMA,
        "manifest": {"complete": True, "source_ids": [row.get("source_id") for row in included]},
        "operations": included,
    }
    return bundle, excluded


__all__ = ["build_export_bundle", "verify_export_bundle", "export_successful_subset"]
