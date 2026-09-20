from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Union


BytesLike = Union[bytes, bytearray, memoryview]


def sha256_bytes(value: BytesLike) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(str(value).encode("utf-8"))


def sha256_structured(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256_text(encoded)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
