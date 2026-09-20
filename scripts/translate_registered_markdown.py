#!/usr/bin/env python3
"""Translate stale Markdown pages declared by a Radar translation task."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.sync_knowledge_sources import translate_markdown_google
except ModuleNotFoundError:  # direct import from the scripts directory
    from sync_knowledge_sources import translate_markdown_google


def _resolve(root: Path, base: Path, raw: str) -> Path:
    value = str(raw or "").replace("\\", "/").strip()
    candidate = root / value if value.startswith("frontend/") else base / value
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"path escapes target root: {raw}")
    return resolved


def translate_task(task: dict[str, Any], target_root: Path) -> dict[str, Any]:
    target_root = Path(target_root).resolve()
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    manifest_rel = str(source.get("stale_manifest") or "")
    source_root_rel = str(source.get("source_root") or "")
    if not manifest_rel:
        return {
            "ok": True,
            "status": "skipped",
            "translated": 0,
            "summary": "未配置 stale 清单",
        }
    manifest_path = _resolve(target_root, target_root, manifest_rel)
    if not manifest_path.is_file():
        return {
            "ok": True,
            "status": "skipped",
            "translated": 0,
            "summary": "stale 清单不存在",
        }
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    pages = payload.get("pages") if isinstance(payload, dict) else []
    if not isinstance(pages, list):
        pages = []
    base = _resolve(target_root, target_root, source_root_rel) if source_root_rel else manifest_path.parent
    max_new = int((task.get("schedule") or {}).get("max_new_items") or len(pages) or 0)
    translated = 0
    errors: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for page in pages:
        if translated >= max_new:
            break
        if not isinstance(page, dict):
            continue
        if str(page.get("translateStatus") or "") in {"translated", "ok"}:
            continue
        try:
            source_path = _resolve(target_root, base, str(page.get("source") or ""))
            zh_path = _resolve(target_root, base, str(page.get("zh") or ""))
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
            text = source_path.read_text(encoding="utf-8", errors="replace")
            translated_text = translate_markdown_google(text)
            if not translated_text.strip():
                raise RuntimeError("翻译结果为空")
            zh_path.parent.mkdir(parents=True, exist_ok=True)
            zh_path.write_text(translated_text.rstrip() + "\n", encoding="utf-8")
            page["translateStatus"] = "translated"
            page["translatedAt"] = now
            translated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{page.get('slug') or page.get('source')}: {exc}")
    if isinstance(payload, dict):
        payload["updatedAt"] = now
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    status = "failed" if errors and not translated else ("partial" if errors else "ok")
    return {
        "ok": not errors,
        "status": status,
        "translated": translated,
        "errors": errors,
        "summary": f"翻译 {translated} 页" + (f" · 失败 {len(errors)} 页" if errors else ""),
    }
