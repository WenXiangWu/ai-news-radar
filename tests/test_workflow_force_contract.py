from __future__ import annotations

from pathlib import Path


def test_request_id_does_not_imply_force():
    workflow = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "RADAR_REQUEST_ID" in workflow
    assert 'if [ -n "${RADAR_REQUEST_ID:-}" ]; then' not in workflow
    assert "inputs.force_all" in workflow
    assert '--max-runtime-minutes "${RADAR_RUN_MAX_RUNTIME_MINUTES:-30}"' in workflow


def test_force_and_bootstrap_are_explicit_inputs():
    workflow = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert "force_all:" in workflow
    assert "bootstrap:" in workflow
    assert "RADAR_FORCE_ALL" in workflow
    assert "RADAR_BOOTSTRAP" in workflow
    assert 'if [ "${RADAR_FORCE_ALL:-}" = "1" ]; then' in workflow
    assert 'if [ "${RADAR_BOOTSTRAP:-}" = "1" ]; then' in workflow
    assert "radar_args+=(--force)" in workflow
    assert "radar_args+=(--bootstrap)" in workflow


def test_publish_continues_when_report_exists_even_if_radar_exit_nonzero():
    workflow = Path(".github/workflows/update-news.yml").read_text(encoding="utf-8")
    assert 'if [ ! -f "$radar_report" ]' in workflow
    assert "scripts/radar_export.py" in workflow
    # Early exit on radar_exit alone must not replace the report-gated path.
    assert 'if [ "$radar_exit" -ne 0 ]; then\n            echo "::warning::Radar run failed or was partial; publishing the failure report' not in workflow
