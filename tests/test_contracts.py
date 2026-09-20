from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar_core.config import RuntimeConfig
from radar_core.contracts import (
    load_protocol,
    load_registry_document,
    validate_contract_document,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "way-registry"


def copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "way"
    for source in FIXTURE_ROOT.rglob("*"):
        destination = target / source.relative_to(FIXTURE_ROOT)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    return target


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_load_protocol_and_registry_normalizes_contract_paths(tmp_path: Path):
    target = copy_fixture(tmp_path)

    protocol = load_protocol(target)
    registry = load_registry_document(target)

    assert protocol["version"] == "1.0"
    assert protocol["registry_index"] == "radar/registry/index.json"
    assert registry["schema"] == "radar-registry/v1"
    assert registry["modules"][0]["manifest"] == "modules/source.example.json"


def test_load_protocol_rejects_missing_protocol(tmp_path: Path):
    target = copy_fixture(tmp_path)
    (target / "radar/protocol.json").unlink()

    with pytest.raises(ValueError, match="protocol"):
        load_protocol(target)


def test_load_protocol_rejects_unsupported_version(tmp_path: Path):
    target = copy_fixture(tmp_path)
    write_json(
        target / "radar/protocol.json",
        {
            "protocol": "way-to-agentic-radar",
            "version": "2.0",
            "registry_index": "radar/registry/index.json",
        },
    )

    with pytest.raises(ValueError, match="version"):
        load_protocol(target)


def test_registry_validation_rejects_duplicate_ids_and_unsafe_paths(tmp_path: Path):
    target = copy_fixture(tmp_path)
    registry = load_registry_document(target)
    registry["modules"].append(dict(registry["modules"][0]))
    registry["managed_roots"].append("../outside")

    errors = validate_contract_document(registry, "radar-registry/v1")

    assert any("duplicate module id" in error for error in errors)
    assert any("unsafe path" in error for error in errors)


def test_registry_validation_rejects_malformed_cron_and_incomplete_task(tmp_path: Path):
    target = copy_fixture(tmp_path)
    registry = load_registry_document(target)
    manifest_path = target / "radar/registry/modules/source.example.json"
    module = json.loads(manifest_path.read_text(encoding="utf-8"))
    task = module["tasks"][0]
    task["schedule"]["cron"] = "not a cron"
    task["adapter"] = ""
    task["output"] = {}

    errors = validate_contract_document(
        {
            "schema": "radar-registry/v1",
            "managed_roots": registry["managed_roots"],
            "modules": [module],
        },
        "radar-registry/v1",
    )

    assert any("cron" in error for error in errors)
    assert any("adapter" in error for error in errors)
    assert any("output" in error for error in errors)


def test_runtime_config_prefers_explicit_environment_and_has_safe_defaults():
    config = RuntimeConfig.from_env(
        {
            "RADAR_WAY_ROOT": "/tmp/way",
            "RADAR_STATE_PATH": "var/custom.sqlite3",
            "RADAR_EXPORT_ROOT": "var/exports",
            "RADAR_TARGET_LOCALES": "zh-CN, en-US",
            "RADAR_DRY_RUN": "true",
            "RADAR_HTTP_TIMEOUT_SECONDS": "17",
        }
    )

    assert config.way_root == Path("/tmp/way")
    assert config.state_path == Path("var/custom.sqlite3")
    assert config.export_root == Path("var/exports")
    assert config.target_locales == ("zh-CN", "en-US")
    assert config.dry_run is True
    assert config.http_timeout_seconds == 17
