from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .ids import translation_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _payload(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


class StateStore:
    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    @classmethod
    def open(cls, path: Path) -> "StateStore":
        target = Path(path)
        if str(target) != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(target))
        connection.row_factory = sqlite3.Row
        store = cls(connection)
        store._initialize()
        return store

    def _initialize(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cursors (
                source_id TEXT PRIMARY KEY,
                cursor_json TEXT NOT NULL,
                run_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_items (
                content_id TEXT PRIMARY KEY,
                source_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revisions (
                revision_id TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                normalizer_version TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(content_id, source_hash, normalizer_version)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                locale TEXT,
                artifact_hash TEXT,
                status TEXT NOT NULL,
                path TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(content_id, revision_id, kind, locale)
            );
            CREATE TABLE IF NOT EXISTS translations (
                translation_key TEXT PRIMARY KEY,
                content_id TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                target_locale TEXT NOT NULL,
                translation_profile TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                provider TEXT,
                source_hash TEXT,
                output_hash TEXT,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(
                    content_id,
                    revision_id,
                    target_locale,
                    translation_profile,
                    policy_version
                )
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                task_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(task_id, run_id)
            );
            """
        )
        self._connection.commit()

    def get_or_create_item(self, content_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _now()
        source_id = str(payload.get("source_id") or "")
        self._connection.execute(
            """
            INSERT INTO content_items(content_id, source_id, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(content_id) DO UPDATE SET
                source_id = excluded.source_id,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (content_id, source_id, _json(payload), now, now),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT content_id, source_id, payload_json FROM content_items WHERE content_id = ?",
            (content_id,),
        ).fetchone()
        result = _payload(json.loads(row["payload_json"]))
        result.update({"content_id": row["content_id"], "source_id": row["source_id"]})
        return result

    def get_item(self, content_id: str) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT content_id, source_id, payload_json
            FROM content_items
            WHERE content_id = ?
            """,
            (content_id,),
        ).fetchone()
        if row is None:
            return None
        result = _payload(json.loads(row["payload_json"]))
        result.update({"content_id": row["content_id"], "source_id": row["source_id"]})
        return result

    def iter_items(self) -> list[Dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT content_id, source_id, payload_json
            FROM content_items
            ORDER BY content_id
            """
        ).fetchall()
        items: list[Dict[str, Any]] = []
        for row in rows:
            payload = _payload(json.loads(row["payload_json"]))
            payload.update({"content_id": row["content_id"], "source_id": row["source_id"]})
            items.append(payload)
        return items

    def find_revisions_by_hash(self, source_hash: str) -> list[Dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT revision_id, content_id, source_hash, normalizer_version,
                   status, payload_json
            FROM revisions
            WHERE source_hash = ?
            ORDER BY revision_id
            """,
            (source_hash,),
        ).fetchall()
        revisions: list[Dict[str, Any]] = []
        for row in rows:
            payload = _payload(json.loads(row["payload_json"]))
            payload.update(
                {
                    "revision_id": row["revision_id"],
                    "content_id": row["content_id"],
                    "source_hash": row["source_hash"],
                    "normalizer_version": row["normalizer_version"],
                    "status": row["status"],
                }
            )
            revisions.append(payload)
        return revisions

    def find_revision(
        self,
        *,
        content_id: str,
        source_hash: str,
        normalizer_version: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT revision_id, content_id, source_hash, normalizer_version,
                   status, payload_json
            FROM revisions
            WHERE content_id = ?
              AND source_hash = ?
              AND normalizer_version = ?
            """,
            (content_id, source_hash, normalizer_version),
        ).fetchone()
        if row is None:
            return None
        payload = _payload(json.loads(row["payload_json"]))
        payload.update(
            {
                "revision_id": row["revision_id"],
                "content_id": row["content_id"],
                "source_hash": row["source_hash"],
                "normalizer_version": row["normalizer_version"],
                "status": row["status"],
            }
        )
        return payload

    def record_revision(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = _now()
        existing = self._connection.execute(
            """
            SELECT content_id, source_hash, normalizer_version
            FROM revisions
            WHERE revision_id = ?
            """,
            (payload["revision_id"],),
        ).fetchone()
        if existing is not None:
            identity = {
                "content_id": payload["content_id"],
                "source_hash": payload["source_hash"],
                "normalizer_version": payload["normalizer_version"],
            }
            if any(existing[field] != value for field, value in identity.items()):
                raise ValueError(
                    f"revision identity conflicts for {payload['revision_id']}"
                )
        self._connection.execute(
            """
            INSERT INTO revisions(
                revision_id, content_id, source_hash, normalizer_version,
                status, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(revision_id) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json
            """,
            (
                payload["revision_id"],
                payload["content_id"],
                payload["source_hash"],
                payload["normalizer_version"],
                payload.get("status", "new"),
                _json(payload),
                now,
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT payload_json FROM revisions WHERE revision_id = ?",
            (payload["revision_id"],),
        ).fetchone()
        return _payload(json.loads(row["payload_json"]))

    def get_translation(self, key: str) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT translation_key, content_id, revision_id, target_locale,
                   translation_profile, policy_version, provider, source_hash,
                   output_hash, status, payload_json
            FROM translations
            WHERE translation_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        result = _payload(json.loads(row["payload_json"]))
        for column in (
            "translation_key",
            "content_id",
            "revision_id",
            "target_locale",
            "translation_profile",
            "policy_version",
            "provider",
            "source_hash",
            "output_hash",
            "status",
        ):
            result[column] = row[column]
        return result

    def upsert_translation(self, payload: Dict[str, Any]) -> None:
        now = _now()
        normalized = dict(payload)
        normalized["translation_key"] = translation_key(
            normalized["content_id"],
            normalized["revision_id"],
            normalized["target_locale"],
            normalized["translation_profile"],
            normalized["policy_version"],
        )
        self._connection.execute(
            """
            INSERT INTO translations(
                translation_key, content_id, revision_id, target_locale,
                translation_profile, policy_version, provider, source_hash,
                output_hash, status, payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                content_id,
                revision_id,
                target_locale,
                translation_profile,
                policy_version
            ) DO UPDATE SET
                translation_key = excluded.translation_key,
                provider = excluded.provider,
                source_hash = excluded.source_hash,
                output_hash = excluded.output_hash,
                status = excluded.status,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                normalized["translation_key"],
                normalized["content_id"],
                normalized["revision_id"],
                normalized["target_locale"],
                normalized["translation_profile"],
                normalized["policy_version"],
                normalized.get("provider"),
                normalized.get("source_hash"),
                normalized.get("output_hash"),
                normalized.get("status", "new"),
                _json(normalized),
                now,
                now,
            ),
        )
        self._connection.commit()

    def get_cursor(self, source_id: str) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            "SELECT source_id, cursor_json, run_id FROM cursors WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "source_id": row["source_id"],
            "cursor": _payload(json.loads(row["cursor_json"])),
            "run_id": row["run_id"],
        }

    def advance_cursor(self, source_id: str, cursor: Dict[str, Any], run_id: str) -> None:
        result = self._connection.execute(
            """
            INSERT INTO cursors(source_id, cursor_json, run_id, updated_at)
            SELECT ?, ?, ?, ?
            WHERE EXISTS (
                SELECT 1 FROM runs WHERE run_id = ? AND status = 'success'
            )
            ON CONFLICT(source_id) DO UPDATE SET
                cursor_json = excluded.cursor_json,
                run_id = excluded.run_id,
                updated_at = excluded.updated_at
            """,
            (source_id, _json(cursor), run_id, _now(), run_id),
        )
        if result.rowcount != 1:
            self._connection.rollback()
            raise ValueError("cursor can only advance for a successful run")
        self._connection.commit()

    def record_run(self, run_id: str, payload: Dict[str, Any]) -> None:
        now = _now()
        self._connection.execute(
            """
            INSERT INTO runs(run_id, status, payload_json, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (run_id, str(payload.get("status") or "running"), _json(payload), now, now),
        )
        self._connection.commit()

    def count_rows(self, table: str) -> int:
        if table not in {
            "sources",
            "cursors",
            "content_items",
            "revisions",
            "artifacts",
            "translations",
            "runs",
            "task_runs",
        }:
            raise ValueError(f"unsupported table: {table}")
        row = self._connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"])

    def close(self) -> None:
        self._connection.commit()
        self._connection.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()
