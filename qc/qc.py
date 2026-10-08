"""Data QC and preparation.

Reads the raw (read-only) ADPC-like file, runs the QC checks and produces the
dataset handed to the PK engine. Every exclusion or fix is written to the QC
log and shown in the report: nothing is changed silently.

This is the only module, together with the R engine, that ever reads data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common.core import sha256_bytes, sha256_file, settings

REQUIRED_COLUMNS = ["USUBJID", "TIME", "NTIME", "AMT", "DV", "EVID", "MDV", "CENS", "LLOQ", "WT", "DVUNIT"]
COVARIATES = ["WT"]


@dataclass
class QCResult:
    prepared: pd.DataFrame
    id_map: pd.DataFrame
    log: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    raw_hash: str = ""
    prepared_hash: str = ""
    prepared_csv: bytes = b""

    @property
    def ok(self) -> bool:
        return not any(e["status"] == "fail" for e in self.log)


class QCError(RuntimeError):
    pass


def _entry(log: list, check: str, status: str, detail: str, n: int = 0, subjects: list[str] | None = None):
    log.append({"check": check, "status": status, "n_records": int(n), "detail": detail,
                "subjects": sorted(set(subjects or []))})


def run_qc(raw_path: Path, blq_method: str) -> QCResult:
    cfg = settings()
    qcfg = cfg["qc"]
    target_unit = cfg["analysis"]["target_conc_unit"]
    log: list[dict[str, Any]] = []

    raw_path = Path(raw_path)
    raw_hash = sha256_file(raw_path)
    df = pd.read_csv(raw_path)
    n_raw = len(df)

    # 1. Required columns -------------------------------------------------
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        _entry(log, "required_columns", "fail", f"Missing required columns: {', '.join(missing)}")
        raise QCError(f"QC failed: missing columns {missing}")
    _entry(log, "required_columns", "pass", f"All {len(REQUIRED_COLUMNS)} required columns present")

    df = df.copy()
    df["_row"] = np.arange(len(df))  # original row number, kept for traceability

    # 2. Units harmonisation ----------------------------------------------
    factors = qcfg["unit_factors"]
    obs = df["EVID"] == 0
    unknown = sorted(set(df.loc[obs, "DVUNIT"].dropna()) - set(factors))
    if unknown:
        _entry(log, "units", "fail", f"Unknown concentration units: {unknown}")
        raise QCError(f"QC failed: unknown units {unknown}")
    conv = obs & (df["DVUNIT"] != target_unit)
    if conv.any():
        f = df.loc[conv, "DVUNIT"].map(factors)
        df.loc[conv, "DV"] = df.loc[conv, "DV"] * f
        df.loc[conv, "LLOQ"] = df.loc[conv, "LLOQ"] * f
        units = ", ".join(sorted(df.loc[conv, "DVUNIT"].unique()))
        df.loc[conv, "DVUNIT"] = target_unit
        _entry(log, "units", "fix",
               f"Converted DV and LLOQ from {units} to {target_unit}",
               int(conv.sum()), df.loc[conv, "USUBJID"].tolist())
    else:
        _entry(log, "units", "pass", f"All concentrations in {target_unit}")

    # 3. Duplicate records ------------------------------------------------
    key = ["USUBJID", "TIME", "EVID"]
    exact_dup = df.duplicated(subset=key + ["DV", "AMT"], keep="first")
    if exact_dup.any():
        _entry(log, "duplicates", "exclude", "Exact duplicate records removed (first kept)",
               int(exact_dup.sum()), df.loc[exact_dup, "USUBJID"].tolist())
        df = df[~exact_dup]
    conflict = df.duplicated(subset=key, keep=False)
    if conflict.any():
        _entry(log, "duplicates", "warn", "Records with same subject/time/event but different values kept for expert review",
               int(conflict.sum()), df.loc[conflict, "USUBJID"].tolist())
    if not exact_dup.any() and not conflict.any():
        _entry(log, "duplicates", "pass", "No duplicate records")

    # 4. Dose / event chronology -----------------------------------------
    doses = df[df["EVID"] == 1]
    bad_amt = doses["AMT"] <= 0
    if bad_amt.any():
        _entry(log, "chronology", "fail", "Dose records with non-positive amount",
               int(bad_amt.sum()), doses.loc[bad_amt, "USUBJID"].tolist())
        raise QCError("QC failed: dose records with non-positive amount")
    first_dose = doses.groupby("USUBJID")["TIME"].min()
    no_dose = sorted(set(df["USUBJID"]) - set(first_dose.index))
    if no_dose:
        sel = df["USUBJID"].isin(no_dose)
        _entry(log, "chronology", "exclude", "Subjects without any dose record excluded",
               int(sel.sum()), no_dose)
        df = df[~sel]
    sorted_df = df.sort_values(["USUBJID", "TIME", "EVID"], ascending=[True, True, False], kind="mergesort")
    if not sorted_df["_row"].equals(df["_row"]):
        _entry(log, "chronology", "fix", "Records re-sorted by subject, time and event (dose first)")
    df = sorted_df
    if not no_dose:
        _entry(log, "chronology", "pass", f"Every subject has a dose record ({len(first_dose)} subjects)")

    # 5. Records before first dose ---------------------------------------
    fd = df["USUBJID"].map(first_dose)
    pre = (df["EVID"] == 0) & (df["TIME"] < fd)
    if pre.any():
        quantified = pre & df["DV"].notna() & (df["CENS"] == 0)
        detail = (f"Observation records before the first dose excluded "
                  f"({int(quantified.sum())} with a quantified concentration > 0, which is not expected before dosing)")
        _entry(log, "records_before_dose", "exclude", detail, int(pre.sum()), df.loc[pre, "USUBJID"].tolist())
        df = df[~pre]
    else:
        _entry(log, "records_before_dose", "pass", "No observation before the first dose")

    # 6. Nominal vs actual time -------------------------------------------
    o = df["EVID"] == 0
    tol = np.maximum(qcfg["ntime_abs_tol_h"], qcfg["ntime_rel_tol"] * df["NTIME"])
    dev = o & ((df["TIME"] - df["NTIME"]).abs() > tol)
    if dev.any():
        _entry(log, "ntime_vs_time", "warn",
               f"Actual time deviates from nominal by more than max({qcfg['ntime_abs_tol_h']} h, "
               f"{int(qcfg['ntime_rel_tol'] * 100)}% of NTIME); records kept, actual TIME used",
               int(dev.sum()), df.loc[dev, "USUBJID"].tolist())
    else:
        _entry(log, "ntime_vs_time", "pass", "All actual times within tolerance of nominal times")

    # 7. Missing values ---------------------------------------------------
    miss_dv = o & df["DV"].isna() & (df["CENS"] == 0)
    if miss_dv.any():
        _entry(log, "missing_dv", "exclude", "Observation records with missing DV and no BLQ flag excluded",
               int(miss_dv.sum()), df.loc[miss_dv, "USUBJID"].tolist())
        df = df[~miss_dv]
    for cov in COVARIATES:
        subj_missing = df.groupby("USUBJID")[cov].apply(lambda s: s.isna().all())
        if subj_missing.any():
            _entry(log, "missing_covariates", "warn",
                   f"{cov} missing for {int(subj_missing.sum())} subjects (not used by catalogue models)",
                   0, subj_missing[subj_missing].index.tolist())
        else:
            _entry(log, "missing_covariates", "pass", f"{cov} available for all subjects")

    # 8. BLQ handling -----------------------------------------------------
    o = df["EVID"] == 0
    blq = o & (df["CENS"] == 1)
    n_obs = int(o.sum())
    frac = blq.sum() / n_obs if n_obs else 0.0
    late = o & (df["NTIME"] >= 24)
    frac_late = (blq & late).sum() / late.sum() if late.any() else 0.0
    status = "warn" if frac > qcfg["blq_warn_fraction"] else "pass"
    _entry(log, "blq_proportion", status,
           f"{int(blq.sum())} BLQ records = {100 * frac:.1f}% of observations "
           f"({100 * frac_late:.1f}% of samples with NTIME >= 24 h)", int(blq.sum()))
    if blq_method == "M1":
        _entry(log, "blq_handling", "exclude", "BLQ method M1: BLQ records discarded before estimation",
               int(blq.sum()), df.loc[blq, "USUBJID"].tolist())
        df = df[~blq]
    elif blq_method == "M3":
        df.loc[blq, "DV"] = df.loc[blq, "LLOQ"]
        _entry(log, "blq_handling", "fix",
               "BLQ method M3: BLQ records kept as left-censored (DV set to LLOQ, CENS = 1)", int(blq.sum()))
    else:
        raise QCError(f"Unknown BLQ method {blq_method!r}")

    # Prepared dataset ----------------------------------------------------
    subjects = sorted(df["USUBJID"].unique())
    id_map = pd.DataFrame({"ID": range(1, len(subjects) + 1), "USUBJID": subjects})
    df = df.merge(id_map, on="USUBJID", how="left")
    df["MDV"] = np.where(df["EVID"] == 1, 1, 0)
    df["DV"] = np.where(df["EVID"] == 1, np.nan, df["DV"])
    cols = ["ID", "TIME", "AMT", "DV", "EVID", "MDV"] + (["CENS"] if blq_method == "M3" else [])
    prepared = df[cols].sort_values(["ID", "TIME", "EVID"], ascending=[True, True, False], kind="mergesort")
    prepared = prepared.reset_index(drop=True)
    csv = prepared.to_csv(index=False, float_format="%.10g", lineterminator="\n").encode()

    po = prepared["EVID"] == 0
    summary = {
        "raw_records": n_raw,
        "prepared_records": len(prepared),
        "subjects": len(subjects),
        "observations_used": int(po.sum()),
        "censored_observations": int(prepared["CENS"].sum()) if "CENS" in prepared else 0,
        "excluded_records": int(sum(e["n_records"] for e in log if e["status"] == "exclude")),
        "fixed_records": int(sum(e["n_records"] for e in log if e["status"] == "fix")),
        "warnings": int(sum(1 for e in log if e["status"] == "warn")),
        "blq_fraction": round(float(frac), 4),
        "blq_fraction_late": round(float(frac_late), 4),
        "ntime_deviations": int(dev.sum()),
        "blq_method": blq_method,
    }
    return QCResult(prepared=prepared, id_map=id_map, log=log, summary=summary, raw_hash=raw_hash,
                    prepared_hash=sha256_bytes(csv), prepared_csv=csv)
