"""Export the run store as a static site (GitHub Pages).

    python -m reporting.site [runs_dir] [site_dir]

Only reports and traceability files are published. The pseudonym map, fit
objects and prepared datasets stay in the run store.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from common.core import runs_dir

PUBLISHED = ["report.html", "manifest.json", "results.json", "comparison.csv", "script.R", "qc_log.json",
             "engine.log", "individual_parameters.csv", "fit_summary.txt"]
PUBLISHED_AUDIT = ["audit_log.jsonl", "llm_calls.jsonl"]  # pseudonymised content only
NEVER = {"pseudonym_map.jsonl"}


def export(src: Path, dst: Path) -> Path:
    from reporting.report import build_history

    dst.mkdir(parents=True, exist_ok=True)
    build_history(dst / "index.html")
    for run in sorted(src.glob("R-*")):
        out = dst / run.name
        out.mkdir(exist_ok=True)
        for name in PUBLISHED:
            if (run / name).exists():
                shutil.copyfile(run / name, out / name)
    (dst / "audit").mkdir(exist_ok=True)
    for name in PUBLISHED_AUDIT:
        if (src / "audit" / name).exists():
            assert name not in NEVER
            shutil.copyfile(src / "audit" / name, dst / "audit" / name)
    if (src / "demo_summary.json").exists():
        shutil.copyfile(src / "demo_summary.json", dst / "demo_summary.json")
    (dst / ".nojekyll").write_text("")
    return dst / "index.html"


if __name__ == "__main__":
    import os

    if len(sys.argv) > 1:
        os.environ["PKPOC_RUNS_DIR"] = sys.argv[1]
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("site")
    print(export(runs_dir(), target))
