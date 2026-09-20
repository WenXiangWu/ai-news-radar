# Radar Task 2 Report

Status: DONE

## Changed files

- `radar_core/hashing.py`
- `radar_core/ids.py`
- `radar_core/storage.py`
- `tests/test_ids_and_hashes.py`
- `tests/test_state_store.py`

## TDD evidence

Red:

```text
python3 -m pytest tests/test_ids_and_hashes.py tests/test_state_store.py -q
ModuleNotFoundError: No module named 'radar_core.hashing'
ModuleNotFoundError: No module named 'radar_core.storage'
```

Green:

```text
python3 -m pytest tests/test_ids_and_hashes.py tests/test_state_store.py -q
7 passed
```

## Design

- URL identity removes fragments, default ports, and tracking parameters while
  preserving meaningful query parameters.
- Content IDs prefer source-native IDs, then canonical URLs, then stable slugs.
- Revision IDs include normalized content and normalizer version.
- Translation keys include content, revision, locale, profile, and policy.
- SQLite stores cursors, content items, revisions, artifacts, translations,
  runs, and task runs with uniqueness constraints for idempotency.

## Concerns

- The current host runtime is Python 3.9.6; the package remains compatible
  with it through postponed annotations, but production should use the planned
  Python 3.11 runtime.

## Review Fixes

The Task 2 review findings were fixed without changing connector, pipeline, or
Way files:

- `advance_cursor()` now inserts or updates a cursor only when its `run_id`
  has an atomic `success` run record; failed and missing runs raise
  `ValueError` and leave the cursor unchanged.
- `upsert_translation()` derives the key from the complete five-tuple
  `(content_id, revision_id, target_locale, translation_profile,
  policy_version)`. A caller-provided mismatched key is normalized, and two
  writes for the same tuple remain idempotent.
- Revision identity columns are checked before an existing revision is
  updated; conflicting `content_id`, `source_hash`, or `normalizer_version`
  values are rejected.
- URL canonicalization removes tracking parameters while preserving the order
  of repeated meaningful query parameters.
- Revision IDs and translation keys now hash canonical structured JSON arrays,
  avoiding delimiter-boundary collisions.

## Review Fix TDD Evidence

Red, after adding the review regression tests and before changing production
code:

```text
python3 -m pytest tests/test_ids_and_hashes.py tests/test_state_store.py -q
6 failed, 5 passed
```

Green and focused regression verification:

```text
python3 -m pytest tests/test_ids_and_hashes.py tests/test_state_store.py -q
11 passed

python3 -m pytest tests/test_contracts.py tests/test_radar_registry.py tests/test_registered_tasks.py tests/test_export_manifest.py -q
27 passed, 1 warning
```

Full Radar verification under Python 3.11:

```text
python3.11 -m pytest -q
275 passed
```

The full suite under the host Python 3.9.6 still stops during collection at
the pre-existing `datetime.UTC` import in `scripts/evaluate_source_overlap.py`;
that file is outside Task 2 and was not modified.
