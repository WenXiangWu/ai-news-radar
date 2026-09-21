from __future__ import annotations

import json
from pathlib import Path

from radar_core.export import (
    _contract_items,
    _contract_revisions,
    build_export_bundle,
    verify_export_bundle,
)
from radar_core.report import build_legacy_free_report, write_run_report
from radar_core.storage import StateStore


def _state(tmp_path: Path) -> StateStore:
    store = StateStore.open(tmp_path / "state.sqlite3")
    store.record_run(
        "run-export",
        {
            "status": "success",
            "source_id": "source.docs",
            "result": {
                "discovered": 1,
                "fetched": 1,
                "translated": 1,
                "quality_failed": 0,
            },
        },
    )
    store.get_or_create_item(
        "content-guide",
        {
            "source_id": "source.docs",
            "title": "Guide",
            "canonical_url": "https://docs.example.test/guide",
        },
    )
    store.record_revision(
        {
            "revision_id": "revision-guide",
            "content_id": "content-guide",
            "source_hash": "source-hash",
            "normalizer_version": "normalizer/v1",
            "status": "published",
        }
    )
    store.upsert_translation(
        {
            "content_id": "content-guide",
            "revision_id": "revision-guide",
            "target_locale": "zh-CN",
            "translation_profile": "prose/v1",
            "policy_version": "policy/v1",
            "provider": "deepseek",
            "source_hash": "source-hash",
            "output_hash": "translated-hash",
            "status": "machine_passed",
            "translated_text": "指南",
        }
    )
    store.record_artifact(
        {
            "artifact_id": "artifact-guide",
            "content_id": "content-guide",
            "revision_id": "revision-guide",
            "kind": "translation",
            "locale": "zh-CN",
            "artifact_hash": "translated-hash",
            "status": "published",
            "path": None,
            "payload": {"translated_text": "指南"},
        }
    )
    return store


def test_build_and_verify_export_bundle(tmp_path: Path):
    store = _state(tmp_path)
    bundle = build_export_bundle("run-export", store, tmp_path / "export")

    assert bundle["schema"] == "radar-content-export/v1"
    bundle_root = tmp_path / "export"
    for relative in (
        "manifest.json",
        "sources.jsonl",
        "items.jsonl",
        "revisions.jsonl",
        "translations.jsonl",
        "checksums.txt",
        "run-report.json",
    ):
        assert (bundle_root / relative).is_file()
    assert list((bundle_root / "artifacts").glob("*.bin"))
    assert verify_export_bundle(bundle_root) == []
    store.close()


def test_export_verification_detects_checksum_tampering(tmp_path: Path):
    store = _state(tmp_path)
    build_export_bundle("run-export", store, tmp_path / "export")
    target = tmp_path / "export" / "translations.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    errors = verify_export_bundle(tmp_path / "export")

    assert any("checksum" in error for error in errors)
    store.close()


def test_export_verification_rejects_unsafe_manifest_path(tmp_path: Path):
    store = _state(tmp_path)
    build_export_bundle("run-export", store, tmp_path / "export")
    manifest_path = tmp_path / "export" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["path"] = "../outside"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    errors = verify_export_bundle(tmp_path / "export")

    assert any("unsafe" in error for error in errors)
    store.close()


def test_export_verification_rejects_partial_run_as_incomplete(tmp_path: Path):
    store = _state(tmp_path)
    store.record_run("run-export", {"status": "partial"})
    build_export_bundle("run-export", store, tmp_path / "export")

    errors = verify_export_bundle(tmp_path / "export")

    assert any("complete" in error or "true" in error for error in errors)
    store.close()


def test_run_report_is_legacy_free_and_writes_json(tmp_path: Path):
    run = {
        "run_id": "run-export",
        "status": "success",
        "result": {
            "discovered": 2,
            "fetched": 2,
            "translated": 2,
        },
    }

    report = build_legacy_free_report(run)
    output = tmp_path / "run-report.json"
    write_run_report(run, output)

    assert report["schema"] == "radar-run-report/v1"
    assert "translated" in report["counts"]
    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == "run-export"


def test_contract_items_select_latest_revision_deterministically():
    revisions = _contract_revisions(
        [
            {
                "revision_id": "later",
                "content_id": "content-guide",
                "source_hash": "later",
                "normalizer_version": "normalizer/v1",
                "payload": {"body": "later"},
                "created_at": "2026-09-20T02:00:00+00:00",
            },
            {
                "revision_id": "earlier",
                "content_id": "content-guide",
                "source_hash": "earlier",
                "normalizer_version": "normalizer/v1",
                "payload": {"body": "earlier"},
                "created_at": "2026-09-20T01:00:00+00:00",
            },
        ]
    )

    items = _contract_items(
        [
            {
                "content_id": "content-guide",
                "source_id": "source.docs",
                "payload": {"title": "Guide"},
            }
        ],
        revisions,
    )

    assert items[0]["revision_id"] == "revision.later"
