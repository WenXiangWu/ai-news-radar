from __future__ import annotations

from radar_core.export import export_successful_subset


def test_export_successful_subset_omits_failed_sources():
    report = {
        "operations": [
            {"source_id": "docs.ollama", "status": "success"},
            {"source_id": "docs.a2a", "status": "failed"},
        ]
    }
    bundle, excluded = export_successful_subset(report)
    assert bundle["manifest"]["complete"] is True
    assert "docs.a2a" in excluded
    assert "docs.ollama" not in excluded
