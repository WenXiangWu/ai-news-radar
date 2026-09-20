from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(value: str | None, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _locales(value: str | None) -> Tuple[str, ...]:
    raw = value or "zh-CN"
    locales = tuple(item.strip() for item in raw.split(",") if item.strip())
    return locales or ("zh-CN",)


@dataclass(frozen=True)
class RuntimeConfig:
    way_root: Path
    state_path: Path
    export_root: Path
    target_locales: Tuple[str, ...] = ("zh-CN",)
    dry_run: bool = False
    http_timeout_seconds: int = 30
    deepseek_api_key: str = ""
    deepseek_api_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    google_translate_endpoint: str = "https://translate.googleapis.com/translate_a/single"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RuntimeConfig":
        values = dict(os.environ if env is None else env)
        return cls(
            way_root=Path(values.get("RADAR_WAY_ROOT", ".")),
            state_path=Path(values.get("RADAR_STATE_PATH", "var/radar/state.sqlite3")),
            export_root=Path(values.get("RADAR_EXPORT_ROOT", "var/radar/exports")),
            target_locales=_locales(values.get("RADAR_TARGET_LOCALES")),
            dry_run=_env_bool(values.get("RADAR_DRY_RUN")),
            http_timeout_seconds=_env_int(
                values.get("RADAR_HTTP_TIMEOUT_SECONDS"),
                30,
            ),
            deepseek_api_key=str(values.get("DEEPSEEK_API_KEY", "")).strip(),
            deepseek_api_base_url=str(
                values.get("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com")
            ).strip()
            or "https://api.deepseek.com",
            deepseek_model=str(values.get("DEEPSEEK_MODEL", "deepseek-chat")).strip()
            or "deepseek-chat",
            google_translate_endpoint=str(
                values.get(
                    "GOOGLE_TRANSLATE_ENDPOINT",
                    "https://translate.googleapis.com/translate_a/single",
                )
            ).strip()
            or "https://translate.googleapis.com/translate_a/single",
        )
