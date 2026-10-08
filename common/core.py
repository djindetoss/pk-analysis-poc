"""Shared helpers: paths, settings, hashing, canonical JSON, append-only audit log."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = (ROOT / "VERSION").read_text().strip()


def runs_dir() -> Path:
    """Run store. Overridable for tests and CI (PKPOC_RUNS_DIR)."""
    d = Path(os.environ.get("PKPOC_RUNS_DIR", ROOT / "runs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_dir() -> Path:
    d = runs_dir() / "audit"
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache
def settings() -> dict[str, Any]:
    with open(ROOT / "config" / "settings.toml", "rb") as fh:
        return tomllib.load(fh)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    """Stable serialisation used for hashing specs and embedding them in scripts."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append-only write. Nothing in the tool ever rewrites or deletes these files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def audit(event: str, **fields: Any) -> dict[str, Any]:
    record = {"ts": now_iso(), "event": event, **fields}
    append_jsonl(audit_dir() / "audit_log.jsonl", record)
    return record
