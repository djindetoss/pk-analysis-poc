import pytest

from validator.validate import OUT_OF_SCOPE_LABEL, validate_spec


@pytest.mark.parametrize("change", [
    {"compartments": 3},
    {"absorption": "zero_order"},
    {"estimation": "IMP"},
    {"blq_method": "M4"},
    {"covariates": [{"parameter": "CL", "time_varying": True}]},
    {"unsupported_features": ["mixture model"]},
    {"route": "iv"},
])
def test_outside_catalogue_is_rejected_never_approximated(spec, change):
    spec.update(change)
    v = validate_spec(spec)
    assert v.status == "out_of_scope"
    assert v.spec is None  # no approximated spec is returned
    assert v.reasons


def test_route_iv_rejected_even_when_other_fields_missing():
    v = validate_spec({"compartments": 2, "route": "iv"})
    assert v.status == "out_of_scope"


def test_label():
    assert OUT_OF_SCOPE_LABEL == "out of scope – expert review"


def test_rejection_is_logged_and_no_run_created(isolated_run_store):
    from common.core import read_jsonl
    from orchestrator import pipeline
    from orchestrator.cli import make_answer, make_confirm

    out = pipeline.submit("Reproduce the target-mediated drug disposition model of XYZ-999",
                          confirm=make_confirm(True), answer=make_answer([]))
    assert out.status == "rejected"
    assert out.run_id is None
    assert not list(isolated_run_store.glob("R-*"))
    events = read_jsonl(isolated_run_store / "audit" / "audit_log.jsonl")
    assert any(e["event"] == "request_rejected" and e["label"] == OUT_OF_SCOPE_LABEL for e in events)
