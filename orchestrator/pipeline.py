"""Orchestrator: assessor request -> harness -> validator -> confirmation -> generator -> QC -> engine -> outputs.

Every step is written to the append-only audit log (runs/audit/audit_log.jsonl).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from common.core import ROOT, TOOL_VERSION, audit, canonical_json, now_iso, settings, sha256_text
from engine.runner import run_engine
from generator.render import render
from harness.extractors import Extractor, get_extractor
from harness.harness import QUESTIONS, interpret
from harness.pseudonymise import pseudonymise
from orchestrator import runs
from qc.qc import QCError, run_qc
from reporting.compare import compare, run_checks, sources_of_difference
from validator.catalogue import load_catalogue, load_schema
from validator.validate import OUT_OF_SCOPE_LABEL, apply_diff, methodology_differs, validate_spec

# confirm(spec, analysis_type, sensitivity_reasons) -> bool
ConfirmFn = Callable[[dict[str, Any], str, list[str]], bool]
# answer(question) -> str
AnswerFn = Callable[[str], str]


@dataclass
class Outcome:
    status: str  # completed | failed | rejected | cancelled | unresolved
    run_id: str | None = None
    spec: dict[str, Any] | None = None
    reasons: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)


@dataclass
class Proposal:
    """A request on its way to execution. Serialisable, so that clarification and
    confirmation can happen in separate interactions (CLI prompt, GitHub comment...)."""
    status: str  # needs_clarification | ready | rejected | unresolved
    prompt_hash: str
    spec: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    missing: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    sensitivity: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    extractor: dict[str, Any] = field(default_factory=dict)
    clarification_rounds: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Proposal":
        return cls(**d)


MAX_CLARIFICATION_ROUNDS = 3


def _declared_methodology(parent_id: str) -> dict[str, Any]:
    """Methodology declared by the applicant = spec of the primary run at the root of the tree."""
    m = runs.load_manifest(parent_id)
    root = runs.load_manifest(m["root_run_id"])
    return root["spec"]


def _extractor_info(extractor: Extractor) -> dict[str, Any]:
    return {"name": extractor.name, "model": extractor.model, "prompt_version": extractor.prompt_version}


def _finalise(p: Proposal) -> Proposal:
    """All required fields known: validate against schema + catalogue, then label the analysis type."""
    v = validate_spec(p.spec)
    if v.status == "incomplete":  # can happen when an answer removes a field
        p.status, p.missing = "needs_clarification", v.missing
        p.questions = [QUESTIONS[f] for f in v.missing]
        return p
    if v.status != "valid":
        label = OUT_OF_SCOPE_LABEL if v.status == "out_of_scope" else v.status
        p.status, p.reasons = "rejected", [label] + v.reasons
        audit("request_rejected", parent_run_id=p.parent_id, job_ids=p.job_ids, validation=v.status,
              label=label, reasons=v.reasons, missing=v.missing)
        return p
    spec = v.spec
    if p.parent_id:
        p.sensitivity = methodology_differs(spec, _declared_methodology(p.parent_id))
        spec["analysis_type"] = ("sensitivity" if p.sensitivity
                                 else runs.load_manifest(p.parent_id)["analysis_type"])
    else:
        spec["analysis_type"] = spec.get("analysis_type") or "primary"
    p.spec, p.status, p.missing, p.questions = spec, "ready", [], []
    audit("spec_proposed", parent_run_id=p.parent_id, job_ids=p.job_ids, spec=spec, sensitivity=p.sensitivity)
    return p


def propose(prompt: str, *, parent_id: str | None = None, extractor: Extractor | None = None) -> Proposal:
    """Interpret an assessor request (new analysis, or refinement of `parent_id`)."""
    extractor = extractor or get_extractor()
    audit("request_received", parent_run_id=parent_id, extractor=extractor.name)
    parent_spec = runs.load_manifest(parent_id)["spec"] if parent_id else None
    res = interpret(prompt, current_spec=parent_spec, extractor=extractor)
    audit("request_interpreted", job_id=res.job_id, parent_run_id=parent_id, status=res.status,
          pseudonymised_prompt=res.pseudonymised_prompt, extracted=res.spec, unsupported=res.unsupported)
    p = Proposal(status="", prompt_hash=sha256_text(prompt), parent_id=parent_id, job_ids=[res.job_id],
                 extractor=_extractor_info(extractor))
    if res.status == "failed":
        p.status = "unresolved"
        p.reasons = [f"extraction failed after retries ({res.error}); please state: " + ", ".join(res.missing)]
        audit("request_unresolved", parent_run_id=parent_id, job_ids=p.job_ids, reasons=p.reasons)
        return p
    p.spec = apply_diff(parent_spec, res.spec) if parent_spec else res.spec
    if res.status == "needs_clarification":
        p.status, p.missing, p.questions = "needs_clarification", res.missing, res.questions
        return p
    return _finalise(p)


def clarify(p: Proposal, reply: str, *, extractor: Extractor | None = None) -> Proposal:
    """Merge an assessor's reply to clarification questions (one reply may answer several)."""
    extractor = extractor or get_extractor()
    for f, q in zip(p.missing, p.questions):
        audit("clarification", job_id=p.job_ids[0], field=f, question=q, answer=pseudonymise(reply).text)
    sub = interpret(reply, current_spec=p.spec, extractor=extractor)
    p.job_ids.append(sub.job_id)
    p.clarification_rounds += 1
    if sub.status == "failed":
        return p  # unchanged: the same questions are asked again
    p.spec = apply_diff(p.spec, sub.spec)
    if sub.unsupported or "unsupported_features" in p.spec:
        return _finalise(p)
    missing = [f for f in load_schema()["required"] if f not in p.spec]
    if missing:
        if p.clarification_rounds >= MAX_CLARIFICATION_ROUNDS:
            p.status, p.reasons = "unresolved", [f"still missing after clarification: {missing}"]
            audit("request_unresolved", parent_run_id=p.parent_id, job_ids=p.job_ids, reasons=p.reasons)
        else:
            p.missing, p.questions = missing, [QUESTIONS[f] for f in missing]
        return p
    return _finalise(p)


def execute_proposal(p: Proposal, confirmation: dict[str, Any], prompt: str | None = None) -> Outcome:
    if p.status != "ready":
        raise ValueError(f"proposal is {p.status}, not ready")
    audit("spec_confirmed", parent_run_id=p.parent_id, job_ids=p.job_ids, spec=p.spec, **confirmation)
    return execute(p.spec, parent_id=p.parent_id, purpose="analysis", sensitivity_fields=p.sensitivity,
                   extractor=p.extractor, job_ids=p.job_ids, confirmation=confirmation, prompt=prompt)


def submit(prompt: str, *, parent_id: str | None = None, confirm: ConfirmFn, answer: AnswerFn,
           extractor: Extractor | None = None) -> Outcome:
    """Synchronous flow (CLI and demo): propose -> clarify -> confirm -> execute."""
    extractor = extractor or get_extractor()
    p = propose(prompt, parent_id=parent_id, extractor=extractor)
    while p.status == "needs_clarification":
        p = clarify(p, "\n".join(answer(q) for q in p.questions), extractor=extractor)
    if p.status != "ready":
        return Outcome(p.status, spec=p.spec, reasons=p.reasons, job_ids=p.job_ids)
    if not confirm(p.spec, p.spec["analysis_type"], p.sensitivity):
        audit("spec_not_confirmed", parent_run_id=parent_id, job_ids=p.job_ids, spec=p.spec)
        return Outcome("cancelled", spec=p.spec, job_ids=p.job_ids)
    confirmation = {"at": now_iso(), "by": getattr(confirm, "who", "assessor"),
                    "mode": getattr(confirm, "mode", "interactive")}
    return execute_proposal(p, confirmation, prompt=prompt)


def execute(spec: dict[str, Any], *, parent_id: str | None, purpose: str, sensitivity_fields: list[str],
            extractor: Extractor | dict[str, Any] | None, job_ids: list[str], confirmation: dict[str, Any],
            prompt: str | None = None) -> Outcome:
    cfg = settings()
    run_id = runs.new_run_id()
    run_dir = runs.create_run_dir(run_id)
    root_id = runs.load_manifest(parent_id)["root_run_id"] if parent_id else run_id
    audit("run_started", run_id=run_id, parent_run_id=parent_id, purpose=purpose,
          analysis_type=spec["analysis_type"])

    rendered = render(spec)
    (run_dir / "script.R").write_text(rendered.script)
    runs.write_json(run_dir / "spec.json", spec)

    raw = ROOT / cfg["analysis"]["raw_data"]
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "parent_run_id": parent_id,
        "root_run_id": root_id,
        "purpose": purpose,
        "analysis_type": spec["analysis_type"],
        "sensitivity_fields": sensitivity_fields,
        "status": "running",
        "created_at": now_iso(),
        "completed_at": None,
        "spec": spec,
        "spec_hash": sha256_text(canonical_json(spec)),
        "methodology_hash": rendered.methodology_hash,
        "template": {"id": rendered.template_id, "version": rendered.template_version,
                     "hash": rendered.template_hash, "catalogue_version": load_catalogue()["catalogue_version"]},
        "script_hash": rendered.script_hash,
        "seed": cfg["analysis"]["seed"],
        "input_data": {"raw_file": str(raw.relative_to(ROOT)), "raw_hash": None, "prepared_hash": None},
        "extractor": {**(extractor if isinstance(extractor, dict) else _extractor_info(extractor) if extractor
                         else {"name": None, "model": None, "prompt_version": None}), "job_ids": job_ids},
        "confirmation": confirmation,
        "packages": {"python": runs.python_packages(), "r": None},
        "environment": runs.environment(),
        "tool_version": TOOL_VERSION,
        "settings": {"tolerance": cfg["analysis"]["tolerance"], "rse_threshold": cfg["analysis"]["rse_threshold"],
                     "qc": {k: v for k, v in cfg["qc"].items() if k != "unit_factors"}},
        "engine": None,
        "outputs": {},
    }
    if prompt is not None:
        manifest["request_prompt_hash"] = sha256_text(prompt)
    runs.write_json(run_dir / "manifest.json", manifest)

    # QC & preparation
    try:
        qc = run_qc(raw, spec["blq_method"])
    except QCError as exc:
        manifest.update(status="failed", completed_at=now_iso(), error=str(exc))
        runs.write_json(run_dir / "manifest.json", manifest)
        audit("run_failed", run_id=run_id, stage="qc", error=str(exc))
        runs.freeze(run_dir)
        return Outcome("failed", run_id=run_id, spec=spec, reasons=[str(exc)], job_ids=job_ids)
    (run_dir / "prepared_data.csv").write_bytes(qc.prepared_csv)
    qc.id_map.to_csv(run_dir / "id_map.csv", index=False)
    runs.write_json(run_dir / "qc_log.json", {"summary": qc.summary, "log": qc.log})
    manifest["input_data"].update(raw_hash=qc.raw_hash, prepared_hash=qc.prepared_hash)
    audit("qc_completed", run_id=run_id, summary=qc.summary)

    # Engine
    outcome = run_engine(run_dir / "script.R", run_dir / "prepared_data.csv", run_dir)
    results = outcome.results
    manifest["engine"] = {"returncode": outcome.returncode, "elapsed_s": outcome.elapsed_s,
                          "resumed_from_checkpoint": (results or {}).get("resumed_from_checkpoint")}
    manifest["packages"]["r"] = (results or {}).get("versions")

    # Checks, comparison, outputs
    checks, comparable = run_checks(results)
    rows = compare(spec.get("reference_values"), results, comparable)
    sources = sources_of_difference(spec, qc.summary, results, rows)
    out = {
        "run_id": run_id, "parent_run_id": parent_id, "analysis_type": spec["analysis_type"],
        "purpose": purpose, "spec": spec, "comparable": comparable, "checks": checks, "comparison": rows,
        "sources_of_difference": sources, "qc": {"summary": qc.summary, "log": qc.log},
        "engine": results,
    }
    runs.write_json(run_dir / "results.json", out)
    _write_comparison_csv(run_dir / "comparison.csv", rows)

    manifest["status"] = "completed" if outcome.ok else "failed"
    manifest["comparable"] = comparable
    manifest["completed_at"] = now_iso()

    from reporting.report import write_run_report  # local import: reporting depends on run store
    write_run_report(run_dir, manifest, out)
    manifest["outputs"] = runs.file_hashes(run_dir, [
        "script.R", "spec.json", "prepared_data.csv", "qc_log.json", "engine_results.json", "results.json",
        "comparison.csv", "individual_parameters.csv", "fit.rds", "report.html", "engine.log"])
    runs.write_json(run_dir / "manifest.json", manifest)
    runs.freeze(run_dir)
    audit("run_completed" if outcome.ok else "run_failed", run_id=run_id, status=manifest["status"],
          comparable=comparable, script_hash=rendered.script_hash, prepared_hash=qc.prepared_hash,
          flagged=sources["flagged_parameters"])
    return Outcome(manifest["status"], run_id=run_id, spec=spec, job_ids=job_ids)


def _write_comparison_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

    cols = ["parameter", "applicant", "independent", "ci_lower", "ci_upper", "rel_diff", "flag", "applicant_in_ci"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------
# Determinism / reproducibility
# --------------------------------------------------------------------------
def rerender_check(run_id: str) -> dict[str, Any]:
    """Re-render a run's script from its stored spec; the hash must be identical."""
    m = runs.load_manifest(run_id)
    stored = runs.run_path(run_id) / "script.R"
    r = render(m["spec"], seed=m["seed"])
    res = {"run_id": run_id, "stored_script_hash": m["script_hash"], "rerendered_script_hash": r.script_hash,
           "identical": r.script_hash == m["script_hash"] == sha256_text(stored.read_text()),
           "template_hash_unchanged": r.template_hash == m["template"]["hash"]}
    audit("determinism_rerender", **res)
    return res


def replay(run_id: str, rel_tol: float = 1e-6) -> dict[str, Any]:
    """Re-execute a run from its stored spec (child run, purpose=replay) and compare estimates."""
    m = runs.load_manifest(run_id)
    spec = dict(m["spec"])
    out = execute(spec, parent_id=run_id, purpose="replay", sensitivity_fields=[], extractor=None, job_ids=[],
                  confirmation={"at": now_iso(), "by": "system", "mode": "replay of a confirmed spec"})
    new = runs.load_manifest(out.run_id)
    a = runs.load_json(runs.run_path(run_id) / "results.json")["engine"]
    b = runs.load_json(runs.run_path(out.run_id) / "results.json")["engine"]
    diffs = {}
    for ea, eb in zip(a["estimates"], b["estimates"]):
        for k in ("estimate", "iiv_cv_percent"):
            x, y = ea.get(k), eb.get(k)
            if x is not None and y is not None:
                diffs[f"{ea['parameter']}.{k}"] = abs(x - y) / max(abs(x), 1e-12)
    max_rel = max(diffs.values()) if diffs else math.inf
    res = {
        "original_run_id": run_id, "replay_run_id": out.run_id,
        "script_hash_identical": new["script_hash"] == m["script_hash"],
        "prepared_data_hash_identical": new["input_data"]["prepared_hash"] == m["input_data"]["prepared_hash"],
        "ofv_original": a["ofv"], "ofv_replay": b["ofv"],
        "max_relative_difference": max_rel, "tolerance": rel_tol,
        "estimates_identical_within_tolerance": max_rel <= rel_tol,
    }
    audit("determinism_replay", **res)
    return res
