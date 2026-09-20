from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ..hashing import sha256_text
from .base import (
    BaseConnector,
    Cursor,
    DiscoveredItem,
    DiscoveryPage,
    RawDocument,
    content_type_for_path,
    stable_manifest_token,
)


DEFAULT_PATTERNS = ("**/*.md", "**/*.mdx", "**/*.txt", "**/*.html", "**/*.json")


class LocalImportConnector(BaseConnector):
    adapter_name = "local_import"
    network = False

    def __init__(self, config: Mapping[str, Any] | None = None):
        super().__init__(config)
        raw_root = (
            self.config.get("root")
            or self.config.get("root_dir")
            or self.config.get("path")
        )
        if not raw_root:
            raise ValueError("local_import requires root or path")
        self.root = Path(raw_root).expanduser().resolve()
        if self.root.is_file():
            self.explicit_files = [self.root]
            self.root = self.root.parent
        else:
            self.explicit_files = []
        self.max_file_bytes = max(
            1,
            int(self.config.get("max_file_bytes", 4 * 1024 * 1024)),
        )

    def discover(self, cursor: Cursor) -> DiscoveryPage:
        current = Cursor.coerce(cursor)
        files = list(self._files())
        manifest: list[dict[str, Any]] = []
        items: list[DiscoveredItem] = []
        for path in files:
            relative = path.relative_to(self.root).as_posix()
            body = self._read(path)
            digest = sha256_text(body)
            manifest.append(
                {
                    "path": relative,
                    "sha256": digest,
                    "size": len(body.encode("utf-8")),
                }
            )
            items.append(
                DiscoveredItem(
                    source_id=self.source_id,
                    native_id=relative,
                    url=f"local://{self.source_id}/{relative}",
                    title=path.stem,
                    content_type=content_type_for_path(path),
                    metadata={
                        "root": str(self.root),
                        "path": relative,
                        "sha256": digest,
                    },
                )
            )

        token = stable_manifest_token(manifest)
        page_cursor = Cursor(
            token=token,
            metadata={"root": str(self.root), "manifest": manifest},
        )
        if current.token == token:
            return DiscoveryPage(
                items=[],
                cursor=page_cursor,
                metadata={"unchanged": True, "root": str(self.root)},
            )
        return DiscoveryPage(
            items=items,
            cursor=page_cursor,
            metadata={"root": str(self.root), "manifest": manifest},
        )

    def fetch(self, item: DiscoveredItem) -> RawDocument:
        relative = str(item.metadata.get("path") or item.native_id)
        path = self._safe_path(relative)
        body = self._read(path)
        digest = sha256_text(body)
        return RawDocument(
            source_id=item.source_id,
            native_id=item.native_id,
            url=item.url,
            title=item.title,
            text=body,
            content_type=item.content_type or content_type_for_path(path),
            published_at=item.published_at,
            etag=f'"{digest}"',
            metadata=dict(item.metadata),
        )

    def _files(self) -> Iterable[Path]:
        if not self.root.exists():
            raise ValueError(f"local_import root does not exist: {self.root}")
        if self.explicit_files:
            paths = self.explicit_files
        else:
            configured = self.config.get("files")
            if configured:
                paths = [
                    self._safe_path(str(value))
                    for value in configured
                ]
            else:
                patterns = self.config.get("patterns") or DEFAULT_PATTERNS
                paths = [
                    path
                    for pattern in patterns
                    for path in self.root.glob(str(pattern))
                ]
                for pattern in patterns:
                    normalized = str(pattern)
                    if normalized.startswith("**/"):
                        paths.extend(self.root.glob(normalized[3:]))
        unique: dict[str, Path] = {}
        for path in paths:
            resolved = path.resolve()
            self._assert_inside(resolved)
            if resolved.is_file():
                unique[resolved.relative_to(self.root).as_posix()] = resolved
        return [unique[key] for key in sorted(unique)]

    def _safe_path(self, value: str) -> Path:
        candidate = Path(value)
        resolved = candidate.expanduser().resolve()
        if not candidate.is_absolute():
            resolved = (self.root / candidate).resolve()
        self._assert_inside(resolved)
        return resolved

    def _assert_inside(self, path: Path) -> None:
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("local_import path escapes configured root") from exc

    def _read(self, path: Path) -> str:
        self._assert_inside(path.resolve())
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError(f"local_import file cannot be read: {path}") from exc
        if size > self.max_file_bytes:
            raise ValueError(
                f"local_import file exceeds {self.max_file_bytes} bytes"
            )
        try:
            with path.open("rb") as handle:
                data = handle.read(self.max_file_bytes + 1)
        except OSError as exc:
            raise ValueError(f"local_import file cannot be read: {path}") from exc
        if len(data) > self.max_file_bytes:
            raise ValueError(
                f"local_import file exceeds {self.max_file_bytes} bytes"
            )
        return data.decode("utf-8", errors="replace")
