"""Pre-comparison checks and the applicant-vs-independent comparison.

The tool flags; it does not conclude. Wording is factual ("differs by",
"above tolerance") and never attributes a difference to an error.
"""
from __future__ import annotations

import math
from typing import Any

from common.core import settings
from validator.catalogue import load_catalogue


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def run_checks(results: dict[str, Any] | None) -> tuple[list[dict[str, str]], bool]:
    """Returns (checks, comparable). A non-converged run is not comparable."""
    cfg = settings()["analysis"]
    if results is None:
        return [{"check": "engine", "status": "fail", "detail": "The PK engine did not produce results"}], False
    checks = []
    conv = results.get("optimizer_message_ok", False)
    checks.append({"check": "convergence", "status": "pass" if conv else "fail",
                   "detail": f"Optimizer message: {results.get('optimizer_message') or '(none)'}"})
    cov = results.get("covariance_ok", False)
    checks.append({"check": "covariance step", "status": "pass" if cov else "warn",
                   "detail": (f"Covariance matrix obtained (method {results.get('covariance_method')})" if cov
                              else "Covariance step failed or not run: standard errors and CIs unavailable")})
    est = [e for e in results["estimates"] if not e.get("fixed")]
    high = [f"{e['parameter']} ({e['rse_percent']:.0f}%)" for e in est
            if _finite(e.get("rse_percent")) and e["rse_percent"] > cfg["rse_threshold"]]
    checks.append({"check": "RSE feasibility", "status": "warn" if high else "pass",
                   "detail": (f"RSE above {cfg['rse_threshold']:.0f}% for: {', '.join(high)}" if high
                              else f"All RSE below {cfg['rse_threshold']:.0f}%")})
    bad = [e["parameter"] for e in results["estimates"]
           if not _finite(e.get("estimate")) or not (1e-4 < e["estimate"] < 1e5)]
    big_iiv = [f"{e['parameter']} ({e['iiv_cv_percent']:.0f}% CV)" for e in results["estimates"]
               if _finite(e.get("iiv_cv_percent")) and e["iiv_cv_percent"] > cfg["iiv_cv_max"]]
    tiny_iiv = [e["parameter"] for e in results["estimates"]
                if _finite(e.get("iiv_cv_percent")) and e["iiv_cv_percent"] < 1]
    problems = ([f"estimate outside plausible bounds: {', '.join(bad)}"] if bad else []) + \
               ([f"IIV above {cfg['iiv_cv_max']:.0f}% CV: {', '.join(big_iiv)}"] if big_iiv else []) + \
               ([f"IIV collapsed to ~0: {', '.join(tiny_iiv)}"] if tiny_iiv else [])
    checks.append({"check": "parameter bounds", "status": "warn" if problems else "pass",
                   "detail": "; ".join(problems) or "All estimates and variances within plausible bounds"})
    info = results.get("run_info") or []
    if info:
        checks.append({"check": "estimation diagnostics", "status": "warn",
                       "detail": "nlmixr2 reported: " + "; ".join(info)})
    return checks, conv


def compare(reference: dict[str, float] | None, results: dict[str, Any] | None, comparable: bool,
            tolerance: float | None = None) -> list[dict[str, Any]]:
    tolerance = settings()["analysis"]["tolerance"] if tolerance is None else tolerance
    labels = load_catalogue()["parameter_labels"]
    reference = reference or {}
    by_param = {e["parameter"]: e for e in (results or {}).get("estimates", [])}
    order = list(labels) + [f"IIV_{p}" for p in labels]  # catalogue order, whatever the extractor's order
    rows = []
    for key, applicant in sorted(reference.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99):
        iiv = key.startswith("IIV_")
        p = key.removeprefix("IIV_")
        e = by_param.get(p, {})
        indep = e.get("iiv_cv_percent") if iiv else e.get("estimate")
        row = {
            "parameter": f"IIV {p} (CV%)" if iiv else labels.get(p, p),
            "key": key,
            "applicant": applicant,
            "independent": indep if _finite(indep) else None,
            "ci_lower": None if iiv else e.get("ci_lower"),
            "ci_upper": None if iiv else e.get("ci_upper"),
            "rel_diff": None,
            "flag": "",
            "applicant_in_ci": None,
        }
        if comparable and _finite(indep):
            row["rel_diff"] = (indep - applicant) / applicant
            row["flag"] = "DIFFERENCE > TOLERANCE" if abs(row["rel_diff"]) > tolerance else ""
            if _finite(row["ci_lower"]) and _finite(row["ci_upper"]):
                row["applicant_in_ci"] = row["ci_lower"] <= applicant <= row["ci_upper"]
        elif not comparable:
            row["flag"] = "NOT COMPARABLE"
        rows.append(row)
    return rows


GENERIC_SOURCES = [
    ("Handling of BLQ observations",
     "M1 (discarding) and M3 (likelihood-based censoring) use different information from late samples, "
     "which mostly informs terminal-phase parameters (V3/F, Q/F) and CL/F."),
    ("Time variables and rounding",
     "Use of actual vs nominal times, and rounding of sampling times, changes the observation design seen by the model."),
    ("Data exclusions and corrections",
     "Records excluded or corrected during data preparation (pre-dose records, unit conversions, outliers) "
     "may differ from the applicant's data handling."),
    ("Estimation settings",
     "Estimation algorithm, interaction term, initial estimates, parameterisation and optimiser tolerances can "
     "lead to different local optima."),
    ("Software and versions",
     "Different software (e.g. NONMEM vs nlmixr2) or versions may give numerically different estimates for the same model."),
]


def sources_of_difference(spec: dict[str, Any], qc_summary: dict[str, Any], results: dict[str, Any] | None,
                          rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Factual context for the assessor. Lists generic factors; does not interpret them."""
    flagged = [r["parameter"] for r in rows if r["flag"] == "DIFFERENCE > TOLERANCE"]
    facts = [
        f"BLQ method used in this run: {spec['blq_method']}; {qc_summary.get('censored_observations', 0)} censored "
        f"observations used; BLQ fraction {100 * qc_summary.get('blq_fraction', 0):.1f}% overall, "
        f"{100 * qc_summary.get('blq_fraction_late', 0):.1f}% of samples with NTIME ≥ 24 h.",
        f"Actual sampling times used; {qc_summary.get('ntime_deviations', 0)} records deviate from nominal time "
        f"beyond the QC tolerance.",
        f"{qc_summary.get('excluded_records', 0)} records excluded and {qc_summary.get('fixed_records', 0)} records "
        f"corrected during data preparation (see QC log).",
        f"Estimation: {spec['estimation']}, template {spec['template']}.",
    ]
    if results:
        v = results.get("versions", {})
        facts.append(f"Software: R {v.get('R')}, nlmixr2 {v.get('nlmixr2')}, nlmixr2est {v.get('nlmixr2est')}, "
                     f"rxode2 {v.get('rxode2')}.")
    return {"flagged_parameters": flagged, "facts": facts,
            "generic_factors": [{"factor": f, "description": d} for f, d in GENERIC_SOURCES]}
