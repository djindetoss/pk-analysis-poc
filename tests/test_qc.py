import pandas as pd

from qc.qc import run_qc

ROWS = [
    # USUBJID, TIME, NTIME, AMT, DV, EVID, MDV, CENS, LLOQ, WT, DVUNIT
    ("S1", -0.5, 0, 0, 120.0, 0, 0, 0, 10, 70, "ng/mL"),   # positive concentration before dose
    ("S1", 0, 0, 100, None, 1, 1, 0, 10, 70, "ng/mL"),
    ("S1", 1, 1, 0, 500.0, 0, 0, 0, 10, 70, "ng/mL"),
    ("S1", 1, 1, 0, 500.0, 0, 0, 0, 10, 70, "ng/mL"),      # exact duplicate
    ("S1", 30, 24, 0, 50.0, 0, 0, 0, 10, 70, "ng/mL"),     # TIME far from NTIME
    ("S1", 72, 72, 0, None, 0, 0, 1, 10, 70, "ng/mL"),     # BLQ
    ("S2", 0, 0, 100, None, 1, 1, 0, 0.01, None, "ug/mL"),
    ("S2", 2, 2, 0, 0.4, 0, 0, 0, 0.01, None, "ug/mL"),    # unit to convert, WT missing
]
COLS = ["USUBJID", "TIME", "NTIME", "AMT", "DV", "EVID", "MDV", "CENS", "LLOQ", "WT", "DVUNIT"]


def _raw(tmp_path):
    p = tmp_path / "raw.csv"
    pd.DataFrame(ROWS, columns=COLS).to_csv(p, index=False)
    return p


def _status(res, check):
    return [e["status"] for e in res.log if e["check"] == check]


def test_every_anomaly_is_logged_and_handled(tmp_path):
    res = run_qc(_raw(tmp_path), "M1")
    assert "exclude" in _status(res, "records_before_dose")
    assert "exclude" in _status(res, "duplicates")
    assert "fix" in _status(res, "units")
    assert "warn" in _status(res, "ntime_vs_time")
    assert "warn" in _status(res, "missing_covariates")
    assert "exclude" in _status(res, "blq_handling")
    s2 = res.prepared[(res.prepared.ID == 2) & (res.prepared.EVID == 0)]
    assert s2.DV.tolist() == [400.0]  # 0.4 ug/mL -> 400 ng/mL
    assert res.summary["observations_used"] == 3


def test_m3_keeps_censored_records_at_lloq(tmp_path):
    res = run_qc(_raw(tmp_path), "M3")
    cens = res.prepared[res.prepared.CENS == 1]
    assert len(cens) == 1 and cens.DV.iloc[0] == 10


def test_prepared_hash_is_deterministic_and_method_specific(tmp_path):
    p = _raw(tmp_path)
    assert run_qc(p, "M1").prepared_hash == run_qc(p, "M1").prepared_hash
    assert run_qc(p, "M1").prepared_hash != run_qc(p, "M3").prepared_hash
