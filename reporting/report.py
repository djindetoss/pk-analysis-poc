"""Self-contained HTML reports: one per run, plus the run-history page."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import jinja2

from common.core import TOOL_VERSION, audit_dir, now_iso, read_jsonl, runs_dir, settings
from validator.catalogue import load_catalogue

TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True,
                          undefined=jinja2.StrictUndefined)

GOF = [("gof_dv_pred.png", "DV vs PRED"), ("gof_dv_ipred.png", "DV vs IPRED"),
       ("gof_cwres_time.png", "CWRES vs TIME"), ("gof_cwres_pred.png", "CWRES vs PRED"),
       ("gof_individual.png", "Individual profiles")]


def fmt(x: Any, digits: int = 3) -> str:
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        if x != x:  # NaN
            return "–"
        return f"{x:.{digits}g}" if abs(x) < 1e5 else f"{x:.3e}"
    return str(x)


def pct(x: Any) -> str:
    return "–" if x is None else f"{100 * x:+.1f}%"


_env.filters["fmt"] = fmt
_env.filters["pct"] = pct
_env.filters["tojson_pretty"] = lambda o: json.dumps(o, indent=2, ensure_ascii=False)


def _img(path: Path) -> str | None:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode() if path.exists() else None


def write_run_report(run_dir: Path, manifest: dict[str, Any], out: dict[str, Any]) -> Path:
    labels = load_catalogue()["parameter_labels"]
    plots = [(title, _img(run_dir / f)) for f, title in GOF]
    html = _env.get_template("report.html.j2").render(
        m=manifest, r=out, labels=labels, plots=[p for p in plots if p[1]],
        tolerance=settings()["analysis"]["tolerance"], generated=now_iso(), tool_version=TOOL_VERSION,
    )
    path = run_dir / "report.html"
    path.write_text(html)
    return path


def _load(run_dir: Path) -> tuple[dict, dict | None]:
    m = json.loads((run_dir / "manifest.json").read_text())
    r = json.loads((run_dir / "results.json").read_text()) if (run_dir / "results.json").exists() else None
    return m, r


def build_history(target: Path | None = None) -> Path:
    """Run tree (parent -> child), side-by-side comparison, rejected requests, determinism checks."""
    base = runs_dir()
    entries = []
    for d in sorted(base.glob("R-*")):
        if (d / "manifest.json").exists():
            m, r = _load(d)
            entries.append({"m": m, "r": r})
    entries.sort(key=lambda e: e["m"]["created_at"])
    by_id = {e["m"]["run_id"]: e for e in entries}
    children: dict[str | None, list] = {}
    for e in entries:
        parent = e["m"]["parent_run_id"] if e["m"]["parent_run_id"] in by_id else None
        children.setdefault(parent, []).append(e)

    def tree(parent=None, depth=0):
        for e in children.get(parent, []):
            yield depth, e
            yield from tree(e["m"]["run_id"], depth + 1)

    # Side-by-side table: union of reference keys and estimated parameters
    keys: list[str] = []
    for e in entries:
        for k in (e["m"]["spec"].get("reference_values") or {}):
            if k not in keys:
                keys.append(k)
    labels = load_catalogue()["parameter_labels"]
    table = []
    for k in keys:
        iiv = k.startswith("IIV_")
        p = k.removeprefix("IIV_")
        row = {"label": f"IIV {p} (CV%)" if iiv else labels.get(p, p), "applicant": None, "cells": []}
        for e in entries:
            ref = (e["m"]["spec"].get("reference_values") or {}).get(k)
            row["applicant"] = row["applicant"] if row["applicant"] is not None else ref
            cmp = {c["key"]: c for c in (e["r"] or {}).get("comparison", [])}
            row["cells"].append(cmp.get(k))
        table.append(row)
    extra_rows = []
    for name, getter in [("OFV", lambda r: (r.get("engine") or {}).get("ofv")),
                         ("Observations used", lambda r: (r.get("engine") or {}).get("n_observations")),
                         ("Censored (M3)", lambda r: (r.get("engine") or {}).get("n_censored")),
                         ("Converged", lambda r: r.get("comparable"))]:
        extra_rows.append({"label": name, "cells": [getter(e["r"]) if e["r"] else None for e in entries]})

    events = read_jsonl(audit_dir() / "audit_log.jsonl")
    rejected = [e for e in events if e["event"] in ("request_rejected", "request_unresolved")]
    for e in rejected:
        jobs = set(e.get("job_ids", []))
        e["prompt"] = next((x.get("pseudonymised_prompt") for x in events
                            if x["event"] == "request_interpreted" and x.get("job_id") in jobs), "")
    determinism = [e for e in events if e["event"].startswith("determinism_")]

    html = _env.get_template("history.html.j2").render(
        entries=entries, tree=list(tree()), table=table, extra_rows=extra_rows, rejected=rejected,
        determinism=determinism, events=events[-200:], tolerance=settings()["analysis"]["tolerance"],
        generated=now_iso(), tool_version=TOOL_VERSION,
    )
    target = target or base / "index.html"
    target.write_text(html)
    return target
