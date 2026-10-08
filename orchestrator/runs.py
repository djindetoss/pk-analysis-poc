"""Run store: run IDs, run directories and manifests. Append-only.

A run directory is created once and never reused; after completion its files
are made read-only. Nothing in the tool deletes a run.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import stat
import subprocess
import sys
import uuid
from importlib import metadata
from pathlib import Path
from typing import Any

from common.core import ROOT, TOOL_VERSION, runs_dir, sha256_file

MANIFEST_REQUIRED = [
    "run_id", "parent_run_id", "root_run_id", "purpose", "analysis_type", "status", "created_at",
    "template", "script_hash", "methodology_hash", "spec_hash", "input_data", "seed",
    "packages", "extractor", "confirmation", "tool_version", "settings",
]


def new_run_id() -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"R-{ts}-{uuid.uuid4().hex[:4]}"


def create_run_dir(run_id: str) -> Path:
    d = runs_dir() / run_id
    d.mkdir(parents=False, exist_ok=False)  # a run id is never reused
    return d


def run_path(run_id: str) -> Path:
    d = runs_dir() / run_id
    if not d.is_dir():
        raise FileNotFoundError(f"unknown run {run_id}")
    return d


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_manifest(run_id: str) -> dict[str, Any]:
    return load_json(run_path(run_id) / "manifest.json")


def list_manifests() -> list[dict[str, Any]]:
    out = []
    for d in sorted(runs_dir().glob("R-*")):
        if (d / "manifest.json").exists():
            out.append(load_json(d / "manifest.json"))
    return sorted(out, key=lambda m: m["created_at"])


def freeze(run_dir: Path) -> None:
    """Make every file of a completed run read-only."""
    for p in run_dir.iterdir():
        if p.is_file():
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def python_packages() -> dict[str, str]:
    pkgs = ["jinja2", "pandas", "numpy", "jsonschema", "typer"]
    out = {"python": platform.python_version()}
    for p in pkgs:
        try:
            out[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            out[p] = None
    return out


def git_commit() -> str | None:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "container_image": os.environ.get("PKPOC_IMAGE"),
        "git_commit": git_commit(),
        "python_executable": sys.executable,
    }


def file_hashes(run_dir: Path, names: list[str]) -> dict[str, str]:
    return {n: sha256_file(run_dir / n) for n in names if (run_dir / n).exists()}
