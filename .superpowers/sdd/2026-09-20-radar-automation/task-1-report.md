# Radar Task 1 Report

Status: DONE

## Changed files

- `radar_core/__init__.py`
- `radar_core/config.py`
- `radar_core/contracts.py`
- `tests/test_contracts.py`
- `tests/fixtures/way-registry/radar/protocol.json`
- `tests/fixtures/way-registry/radar/registry/index.json`
- `tests/fixtures/way-registry/radar/registry/modules/source.example.json`

## TDD evidence

Red:

```text
python3 -m pytest tests/test_contracts.py -q
ModuleNotFoundError: No module named 'radar_core'
```

Green:

```text
python3 -m pytest tests/test_contracts.py -q
6 passed
python3 -m pytest tests/test_contracts.py tests/test_radar_registry.py tests/test_registered_tasks.py tests/test_export_manifest.py -q
21 passed, 1 warning
```

The warning is the existing urllib3 LibreSSL warning from the host Python 3.9
runtime.

## Design

- The loader validates the existing `way-to-agentic-radar` protocol and 1.x
  version, then loads the referenced registry index.
- Registry validation is structural and side-effect free: it checks IDs,
  manifest/output paths, schedules, timezones, task adapters, and declarations.
- Runtime configuration is environment-driven and keeps provider secrets out
  of logs and serialized contracts.

## Concerns

- The host exposes Python 3.9.6 while the long-term architecture plan targets
  Python 3.11. The implementation remains compatible with the current test
  runtime and should be rechecked under the production interpreter.

## Review Fix: Commit 33f01eb

The loader now supports the actual Way `way-content-registry/v1` index while
retaining compatibility with the legacy `radar-registry/v1` fixture. For the
Way schema it recursively reads every JSON manifest under the four indexed
manifest roots, validates manifest structure and cross-references, and returns
normalized `declarations` (also exposed as `modules` for discovery consumers)
with stable source/entity/surface IDs, schedule, translation profile, and
output fields. Legacy module references are now loaded and validated before
they are returned, so adapter, output, schedule, and task errors cannot be
hidden behind the index.

Cron validation now checks field ranges, ranges/lists, and positive steps.
Protocol and runtime configuration paths ending in JSON are normalized to the
Radar-owned SQLite state path while job-report and export-report paths remain
unchanged; the original Way JSON state path is retained as `way_state_path`
for visibility.

### Review-fix TDD evidence

Red after adding regression tests:

```text
python3 -m pytest tests/test_contracts.py -q
6 failed, 6 passed
```

The failures covered the unsupported Way schema, skipped referenced manifest
validation, `99 * * * *`, `*/0 * * * *`, JSON state normalization, and runtime
state normalization.

Green:

```text
python3 -m pytest tests/test_contracts.py -q
12 passed
python3 -m pytest tests/test_contracts.py tests/test_radar_registry.py tests/test_registered_tasks.py tests/test_export_manifest.py -q
27 passed, 1 warning
python3 -m pytest -q --ignore=tests/test_source_overlap.py
265 passed, 1 warning
python3 -m compileall -q radar_core
git diff --check
```

The unfiltered full suite remains blocked during collection by the existing
Python 3.9 incompatibility in `tests/test_source_overlap.py`, which imports
`datetime.UTC`. No Way files were modified.

## Re-review Fix: Preserve Explicit Way Modules

The re-review found that the Way loader replaced the index's explicit
`modules` references with the four-root normalized declarations. The fixture
also did not include the five current Way module manifests, so this could
silently drop DeepSeek Harness, Cordis, coding-tools, Qdrant, and App Platform
from Radar discovery.

The loader now:

- loads and validates every `index.modules[].manifest` reference;
- keeps those normalized module manifests in `registry["modules"]`;
- keeps four-root source/entity/surface/editorial expansions separately in
  `registry["declarations"]`;
- validates module schema, referenced module ID, adapter, output, schedule,
  and globally unique task IDs;
- resolves every indexed root and explicit module manifest beneath the
  registry root, rejecting symlink/path escapes.

The actual Way registry fixture now contains all five referenced module
manifests. The tests assert all five IDs are retained, mutate a module task to
prove referenced content validation, cover output/cron/duplicate-task-ID
validation, and verify a symlink escape is rejected.

### Re-review TDD evidence

Red:

```text
python3 -m pytest tests/test_contracts.py -q
3 failed, 11 passed
```

The failures demonstrated dropped explicit modules, skipped referenced module
validation, and accepted an escaping module symlink.

Green:

```text
python3 -m pytest tests/test_contracts.py -q
17 passed

python3 -m pytest tests/test_contracts.py tests/test_radar_registry.py tests/test_registered_tasks.py tests/test_export_manifest.py -q
32 passed, 1 warning

python3 -m pytest -q --ignore=tests/test_source_overlap.py
274 passed, 1 warning
```

The unfiltered suite still stops during collection at
`tests/test_source_overlap.py` because the host Python 3.9 runtime does not
provide `datetime.UTC`. The change is limited to this Radar repository; no
Way files were modified.
