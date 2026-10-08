from harness.harness import interpret
from validator.validate import apply_diff, methodology_differs, validate_spec


def test_diff_overwrites_and_none_removes(spec):
    child = apply_diff(spec, {"blq_method": "M3", "reference_values": None})
    assert child["blq_method"] == "M3"
    assert "reference_values" not in child
    assert spec["blq_method"] == "M1"  # parent untouched


def test_refinement_prompt_gives_minimal_diff_and_revalidates(spec):
    parent = validate_spec(spec).spec
    res = interpret("Same model, but handle BLQ with M3.", current_spec=parent)
    assert res.spec == {"blq_method": "M3"}
    child = validate_spec(apply_diff(parent, res.spec))
    assert child.ok and child.spec["blq_method"] == "M3"


def test_methodology_change_is_detected(spec):
    parent = validate_spec(spec).spec
    child = apply_diff(parent, {"blq_method": "M3"})
    assert methodology_differs(child, parent) == ["blq_method"]
    same = apply_diff(parent, {"reference_values": {"CL": 4.0}})
    assert methodology_differs(same, parent) == []


def test_child_run_with_changed_methodology_is_sensitivity(spec, monkeypatch):
    from orchestrator import pipeline, runs
    from orchestrator.cli import make_answer, make_confirm

    seen = {}

    def fake_execute(s, **kw):
        seen["spec"], seen["kw"] = s, kw
        return pipeline.Outcome("completed", run_id="X", spec=s)

    parent = validate_spec(spec).spec
    monkeypatch.setattr(runs, "load_manifest", lambda rid: {"spec": parent, "root_run_id": "R0",
                                                             "analysis_type": "primary"})
    monkeypatch.setattr(pipeline, "execute", fake_execute)
    pipeline.submit("Same model, but handle BLQ with M3.", parent_id="R0",
                    confirm=make_confirm(True), answer=make_answer([]))
    assert seen["spec"]["analysis_type"] == "sensitivity"
    assert seen["kw"]["sensitivity_fields"] == ["blq_method"]
