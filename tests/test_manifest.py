import json

import pandas as pd
import pytest

from orchestrator import pipeline, runs
from orchestrator.runs import MANIFEST_REQUIRED
from validator.validate import validate_spec
from tests.test_qc import COLS, ROWS


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    """Replace Rscript by a stub writing plausible engine results."""
    from engine.runner import EngineOutcome

    raw = tmp_path / "raw.csv"
    pd.DataFrame(ROWS, columns=COLS).to_csv(raw, index=False)
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setitem(pipeline.settings()["analysis"], "raw_data", "raw.csv")

    def run_engine(script, data, out_dir):
        res = {
            "ofv": 100.0, "aic": 120.0, "n_subjects": 2, "n_observations": 3, "n_censored": 0,
            "optimizer_message": "relative convergence (4)", "optimizer_message_ok": True,
            "covariance_method": "r,s", "covariance_ok": True, "run_info": [], "warnings": [],
            "resumed_from_checkpoint": False, "elapsed_seconds": 1.0, "seed": 1,
            "versions": {"R": "4.x", "nlmixr2": "x"},
            "estimates": [{"parameter": p, "estimate": v, "rse_percent": 5.0, "ci_lower": v * .9,
                           "ci_upper": v * 1.1, "fixed": False, "iiv_cv_percent": 30.0 if p == "CL" else None,
                           "shrinkage_percent": None}
                          for p, v in [("CL", 4.0), ("V2", 36.0), ("Q", 5.0), ("V3", 119.0), ("KA", 1.1)]],
            "residual_error": {},
        }
        (out_dir / "engine_results.json").write_text(json.dumps(res))
        (out_dir / "engine.log").write_text("stub\n")
        return EngineOutcome(0, 0.1, res, out_dir / "engine.log")

    monkeypatch.setattr(pipeline, "run_engine", run_engine)


def _execute(spec):
    s = validate_spec(spec).spec
    return pipeline.execute(s, parent_id=None, purpose="analysis", sensitivity_fields=[], extractor=None,
                            job_ids=["J-test"], confirmation={"at": "now", "by": "test", "mode": "test"})


def test_manifest_is_complete(spec, fake_engine):
    out = _execute(spec)
    m = runs.load_manifest(out.run_id)
    missing = [k for k in MANIFEST_REQUIRED if k not in m]
    assert not missing
    assert m["status"] == "completed"
    assert len(m["script_hash"]) == 64
    assert len(m["input_data"]["raw_hash"]) == 64 and len(m["input_data"]["prepared_hash"]) == 64
    assert m["template"]["id"] == "pk_2cmt_oral_v1" and m["template"]["version"]
    assert m["seed"] and m["packages"]["python"]["python"] and m["packages"]["r"]
    assert m["parent_run_id"] is None and m["root_run_id"] == out.run_id
    assert set(m["outputs"]) >= {"script.R", "report.html", "results.json", "comparison.csv"}


def test_run_files_are_frozen_and_ids_never_reused(spec, fake_engine):
    out = _execute(spec)
    d = runs.run_path(out.run_id)
    # check the permission bits (os.access is always True for root, e.g. in the container)
    assert all(not (f.stat().st_mode & 0o222) for f in d.iterdir() if f.is_file())
    with pytest.raises(FileExistsError):
        runs.create_run_dir(out.run_id)


def test_comparison_flags_without_concluding(spec, fake_engine):
    out = _execute(spec)
    res = json.loads((runs.run_path(out.run_id) / "results.json").read_text())
    q = next(r for r in res["comparison"] if r["key"] == "Q")
    assert q["flag"] == "DIFFERENCE > TOLERANCE"  # 5.0 vs 8.1
    text = (runs.run_path(out.run_id) / "report.html").read_text().lower()
    for word in ("error by the applicant", "applicant made", "incorrect", "wrong"):
        assert word not in text


def test_history_page_renders_with_runs_rejections_and_audit_events(spec, fake_engine):
    from orchestrator.cli import make_answer, make_confirm
    from reporting.report import build_history
    from reporting.site import export

    out = _execute(spec)
    pipeline.submit("Add a time-varying covariate on CL", parent_id=out.run_id,
                    confirm=make_confirm(True), answer=make_answer([]))
    html = build_history().read_text()
    assert out.run_id in html and "out of scope" in html
    store = runs.run_path(out.run_id).parent
    site = export(store, store.parent / "site")
    assert not (site.parent / "audit" / "pseudonym_map.jsonl").exists()
