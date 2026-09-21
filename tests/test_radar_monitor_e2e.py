from __future__ import annotations

import json
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from scripts.build_radar_monitor_snapshot import build_monitor_snapshot


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        return


def _write_registry(root: Path) -> None:
    registry = root / "radar" / "registry"
    (registry / "modules").mkdir(parents=True, exist_ok=True)
    (registry / "index.json").write_text(
        json.dumps(
            {
                "modules": [
                    {
                        "id": "knowledge.example",
                        "manifest": "modules/knowledge.example.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (registry / "modules/knowledge.example.json").write_text(
        json.dumps(
            {
                "id": "knowledge.example",
                "kind": "knowledge",
                "enabled": True,
                "display": {"name": "Example knowledge"},
                "source": {
                    "id": "source.knowledge.example",
                    "adapter": "llms_txt",
                    "locator": "https://example.test/llms.txt",
                    "schedule": {"cron": "17 * * * *", "timezone": "UTC"},
                },
                "tasks": [
                    {
                        "id": "task.knowledge.example.sync",
                        "adapter": "llms_txt",
                        "schedule": {"cron": "17 * * * *", "timezone": "UTC"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_reports(root: Path) -> tuple[Path, Path, Path]:
    run_report = root / "run-report.json"
    run_report.write_text(
        json.dumps(
            {
                "schema": "radar-run-report/v1",
                "run_id": "run-e2e",
                "status": "success",
                "started_at": "2026-09-21T03:17:00+00:00",
                "finished_at": "2026-09-21T03:17:02+00:00",
                "duration_ms": 2000,
                "providers": [
                    {
                        "id": "google",
                        "name": "Google Translate",
                        "configured": True,
                        "status": "configured",
                    }
                ],
                "operations": [
                    {
                        "source_id": "source.knowledge.example",
                        "module_id": "knowledge.example",
                        "module_name": "Example knowledge",
                        "task_id": "task.knowledge.example.sync",
                        "adapter": "llms_txt",
                        "status": "success",
                        "revisions_new": 1,
                        "translated": 1,
                        "baseline_after": {
                            "baseline_fingerprint": "baseline-e2e",
                            "cursor_run_id": "run-e2e",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    validation = root / "source-validation.json"
    validation.write_text(
        json.dumps(
            {
                "schema": "radar-source-validation/v1",
                "status": "ok",
                "sources": [
                    {
                        "source_id": "source.knowledge.example",
                        "module_id": "knowledge.example",
                        "status": "ok",
                        "adapter": "llms_txt",
                        "health": {"status": "healthy"},
                        "baseline_status": "verified",
                        "cursor_status": "present",
                        "live": {"enabled": True, "latency_ms": 12},
                        "errors": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    update = root / "update-report.json"
    update.write_text(
        json.dumps(
            {
                "schema": "radar-update-report/v1",
                "run_id": "run-e2e",
                "generated_at": "2026-09-21T03:17:02+00:00",
                "status": "success",
                "modules": [
                    {
                        "module_id": "knowledge.example",
                        "name": "Example knowledge",
                        "kind": "knowledge",
                        "status": "success",
                        "updated": 1,
                        "translated": 1,
                        "translation_links": [
                            {
                                "content_id": "content-e2e",
                                "url": "/data/radar-reports/run-e2e.json",
                            }
                        ],
                        "errors": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return run_report, validation, update


def test_monitor_bundle_is_reachable_with_project_relative_paths(tmp_path: Path):
    way_root = tmp_path / "way"
    _write_registry(way_root)
    run_report, validation, update = _write_reports(tmp_path)
    data = tmp_path / "site" / "data"
    monitor = tmp_path / "site" / "monitor"
    data.mkdir(parents=True)
    monitor.mkdir(parents=True)
    source_monitor = Path(__file__).parents[1] / "monitor"
    for asset in ("index.html", "monitor.js", "monitor.css"):
        shutil.copy2(source_monitor / asset, monitor / asset)

    snapshot = build_monitor_snapshot(
        way_root,
        run_report,
        validation,
        update,
        public_prefix="/data",
        reports_prefix="/data/radar-reports",
    )
    (data / "radar-monitor.json").write_text(
        json.dumps(snapshot),
        encoding="utf-8",
    )
    (data / "radar-update-report.json").write_text(
        update.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (data / "radar-reports").mkdir()
    (data / "radar-reports/index.json").write_text(
        json.dumps(
            {
                "schema": "radar-report-index/v1",
                "reports": [
                    {
                        "date": "2026-09-21",
                        "status": "success",
                        "path": "/data/radar-reports/run-e2e.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: _QuietHandler(
            *args,
            directory=str(tmp_path / "site"),
            **kwargs,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        for path in (
            "/monitor/",
            "/monitor/monitor.js",
            "/monitor/monitor.css",
            "/data/radar-monitor.json",
            "/data/radar-update-report.json",
            "/data/radar-reports/index.json",
        ):
            with urlopen(base + path, timeout=3) as response:
                assert response.status == 200, path
        with urlopen(base + "/data/radar-monitor.json", timeout=3) as response:
            served = json.load(response)
        assert served["summary"]["modules"] == 1
        assert served["summary"]["updated"] == 1
        assert served["run"]["status"] == "success"
    finally:
        server.shutdown()
        thread.join(timeout=3)
