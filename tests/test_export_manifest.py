from __future__ import annotations

import pytest

from scripts.write_export_manifest import build_export_manifest


def test_export_manifest_contains_declared_generated_paths():
    registry = {
        "modules": [
            {
                "display": {"target_path": "frontend/path/frameworks/demo"},
                "tasks": [
                    {"output": {"path": "frontend/sources/demo/articles"}},
                    {"output": {"path": "frontend/path/frameworks/hubs.json"}},
                ],
            }
        ]
    }

    manifest = build_export_manifest(registry)

    assert manifest["schema"] == "radar-export-manifest/v1"
    assert "frontend/frontier/radar-data" in manifest["paths"]
    assert "frontend/sources/demo/articles" in manifest["paths"]
    assert "frontend/path/frameworks/hubs.json" in manifest["paths"]


def test_export_manifest_rejects_absolute_and_parent_paths():
    with pytest.raises(ValueError):
        build_export_manifest(
            {"modules": [{"tasks": [{"output": {"path": "../outside"}}]}]}
        )
