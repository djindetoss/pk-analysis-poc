"""Static site (GitHub Pages).

    python -m reporting.site --store <run-store dir> --out site     # full portal
    python -m reporting.site <runs dir> <site dir>                   # one run store only (legacy)

Portal layout:  /index.html   home: new request form, requests, links
                /runs/        assessor runs (store/runs) and their history page
                /demo/        reference demo produced by CI (store/demo)

Only reports and traceability files are published. The pseudonym map stays local.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

import jinja2

from common.core import TOOL_VERSION, now_iso, read_jsonl, settings

PUBLISHED = ["report.html", "manifest.json", "results.json", "comparison.csv", "script.R", "qc_log.json",
             "engine.log", "individual_parameters.csv", "fit_summary.txt"]
PUBLISHED_AUDIT = ["audit_log.jsonl", "llm_calls.jsonl"]  # pseudonymised content only
NEVER = {"pseudonym_map.jsonl"}

EXAMPLES = [
    ("Reproduce applicant's model",
     "Reproduce the applicant's model for XYZ-123: two compartments, first-order absorption, combined error, "
     "IIV on CL, V2 and KA, FOCE-I. Reported: CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h, "
     "IIV on CL 30% CV."),
    ("Fully specified (M3)",
     "Two-compartment oral model with first-order absorption, combined error, IIV on CL, V2 and KA, FOCE-I, "
     "BLQ handled with M3. Applicant reported CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h."),
    ("Different values (flags)",
     "Reproduce the applicant's model for XYZ-777: two compartments, first-order absorption, combined error, "
     "IIV on CL, V2 and KA, FOCE-I, BLQ discarded (M1). Reported: CL/F 6.0 L/h, V2/F 35 L, Q/F 4.0 L/h, "
     "V3/F 120 L, KA 1.1 /h."),
    ("Out of scope",
     "Reproduce the applicant's model with weight as a time-varying covariate on CL and a transit-compartment "
     "absorption."),
    ("IV (no template)",
     "Two-compartment model after IV infusion, combined error, IIV on CL and V2, FOCE-I, M3 for BLQ."),
]

STATUS_LABEL = {"needs_clarification": "awaiting answer", "awaiting_confirmation": "awaiting /confirm",
                "running": "running", "completed": "completed", "failed": "failed",
                "rejected": "out of scope", "cancelled": "cancelled", "unresolved": "unresolved", "new": "new"}
STATUS_CLASS = {"completed": "pass", "failed": "fail", "rejected": "fail", "needs_clarification": "warn",
                "awaiting_confirmation": "warn", "running": "fix"}


def export(src: Path, dst: Path) -> Path:
    """Publish one run store (runs + history + pseudonymised audit) into dst."""
    from reporting.report import build_history

    previous = os.environ.get("PKPOC_RUNS_DIR")
    os.environ["PKPOC_RUNS_DIR"] = str(src)
    try:
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
    finally:
        if previous is None:
            os.environ.pop("PKPOC_RUNS_DIR", None)
        else:
            os.environ["PKPOC_RUNS_DIR"] = previous
    return dst / "index.html"


def build_portal(store: Path, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (store / "runs").mkdir(parents=True, exist_ok=True)
    export(store / "runs", out / "runs")
    has_demo = (store / "demo").is_dir() and any((store / "demo").glob("R-*"))
    if has_demo:
        export(store / "demo", out / "demo")

    requests = [json.loads(p.read_text()) for p in (store / "requests").glob("*.json")] \
        if (store / "requests").is_dir() else []
    requests.sort(key=lambda r: r["updated_at"], reverse=True)
    events = read_jsonl(store / "runs" / "audit" / "audit_log.jsonl")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    html = env.get_template("home.html.j2").render(
        requests=requests, repo=settings()["github"]["repo"], has_demo=has_demo,
        n_runs=len(list((store / "runs").glob("R-*"))),
        n_rejected=sum(1 for e in events if e["event"] == "request_rejected"),
        examples=[{"label": l, "text": t} for l, t in EXAMPLES],
        status_label=STATUS_LABEL, status_class=STATUS_CLASS, tool_version=TOOL_VERSION, generated=now_iso(),
    )
    (out / "index.html").write_text(html)
    (out / ".nojekyll").write_text("")
    return out / "index.html"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("legacy", nargs="*", help="<runs dir> [<site dir>]")
    ap.add_argument("--store", type=Path)
    ap.add_argument("--out", type=Path, default=Path("site"))
    a = ap.parse_args()
    if a.store:
        print(build_portal(a.store, a.out))
    else:
        src = Path(a.legacy[0]) if a.legacy else Path(os.environ.get("PKPOC_RUNS_DIR", "runs"))
        dst = Path(a.legacy[1]) if len(a.legacy) > 1 else a.out
        print(export(src, dst))
