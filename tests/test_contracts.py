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
ACTUAL_WAY_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "actual-way-registry"


def copy_fixture(tmp_path: Path, source_root: Path = FIXTURE_ROOT) -> Path:
    target = tmp_path / source_root.name
    for source in source_root.rglob("*"):
        destination = target / source.relative_to(source_root)
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


def test_load_actual_way_registry_returns_normalized_declarations(tmp_path: Path):
    target = copy_fixture(tmp_path, ACTUAL_WAY_FIXTURE_ROOT)

    registry = load_registry_document(target)

    assert registry["schema"] == "way-content-registry/v1"
    assert {
        module["id"] for module in registry["modules"]
    } == {
        "framework.deepseek-harness",
        "framework.cordis",
        "source.coding-tools",
        "source.qdrant.editorial",
        "framework.app-platform.editorial",
    }
    assert len(registry["modules"]) == 5
    assert registry["modules"] != registry["declarations"]
    assert registry["manifests"]["sources"][0]["id"] == "source.example.official-blog"
    declaration = registry["declarations"][0]
    assert declaration["source_id"] == "source.example.official-blog"
    assert declaration["entity_id"] == "framework.example"
    assert declaration["surface_id"] == "surface.example-framework.docs"
    assert declaration["schedule"]["cron"] == "17 4 * * *"
    assert declaration["translation_profile"] == "prose/v1"
    assert declaration["output"] == {"path": "frontend/path/frameworks/example"}


def test_loader_validates_actual_referenced_module_contents(tmp_path: Path):
    target = copy_fixture(tmp_path, ACTUAL_WAY_FIXTURE_ROOT)
    manifest_path = target / "radar/registry/modules/framework.cordis.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tasks"][0]["adapter"] = ""
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="adapter"):
        load_registry_document(target)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("output", "output"),
        ("schedule", "cron"),
        ("task_id", "duplicate task id"),
    ],
)
def test_loader_validates_explicit_module_task_contract(
    tmp_path: Path,
    mutation: str,
    message: str,
):
    target = copy_fixture(tmp_path, ACTUAL_WAY_FIXTURE_ROOT)
    manifest_path = target / "radar/registry/modules/framework.cordis.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "output":
        manifest["tasks"][0]["output"] = {}
    elif mutation == "schedule":
        manifest["tasks"][0]["schedule"]["cron"] = "99 * * * *"
    else:
        manifest["tasks"][1]["id"] = manifest["tasks"][0]["id"]
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match=message):
        load_registry_document(target)


def test_loader_rejects_explicit_module_manifest_symlink_escape(tmp_path: Path):
    target = copy_fixture(tmp_path, ACTUAL_WAY_FIXTURE_ROOT)
    index_path = target / "radar/registry/index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["modules"][0]["manifest"] = "modules/escaped.json"
    write_json(index_path, index)

    outside = tmp_path / "outside-module.json"
    write_json(outside, {"schema": "way-content-registry/v1/module"})
    (target / "radar/registry/modules/escaped.json").symlink_to(outside)

    with pytest.raises(ValueError, match="outside registry root"):
        load_registry_document(target)


def test_loader_validates_referenced_manifest_contents(tmp_path: Path):
    target = copy_fixture(tmp_path)
    manifest_path = target / "radar/registry/modules/source.example.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tasks"][0]["adapter"] = ""
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="adapter"):
        load_registry_document(target)


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


@pytest.mark.parametrize("cron", ["99 * * * *", "*/0 * * * *"])
def test_registry_validation_rejects_out_of_range_cron(cron: str, tmp_path: Path):
    target = copy_fixture(tmp_path)
    registry = load_registry_document(target)
    manifest_path = target / "radar/registry/modules/source.example.json"
    module = json.loads(manifest_path.read_text(encoding="utf-8"))
    module["tasks"][0]["schedule"]["cron"] = cron

    errors = validate_contract_document(
        {
            "schema": "radar-registry/v1",
            "managed_roots": registry["managed_roots"],
            "modules": [module],
        },
        "radar-registry/v1",
    )

    assert any("cron" in error for error in errors)


def test_load_protocol_normalizes_way_json_state_path(tmp_path: Path):
    target = copy_fixture(tmp_path, ACTUAL_WAY_FIXTURE_ROOT)

    protocol = load_protocol(target)

    assert protocol["state_path"] == "var/radar/state.sqlite3"
    assert protocol["way_state_path"] == "frontend/frontier/radar-data/registry-state.json"


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


def test_runtime_config_normalizes_non_sqlite_state_path():
    config = RuntimeConfig.from_env({"RADAR_STATE_PATH": "var/radar/state.json"})

    assert config.state_path == Path("var/radar/state.sqlite3")
