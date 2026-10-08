from generator.render import render, template_hash
from validator.validate import validate_spec


def test_same_spec_same_script_hash(spec):
    s = validate_spec(spec).spec
    a, b = render(s), render(dict(reversed(list(s.items()))))  # key order is irrelevant
    assert a.script == b.script
    assert a.script_hash == b.script_hash


def test_methodology_change_changes_hash_reference_values_do_not(spec):
    s = validate_spec(spec).spec
    base = render(s).script_hash
    assert render({**s, "blq_method": "M3"}).script_hash != base
    assert render({**s, "reference_values": {"CL": 9.9}}).script_hash == base
    assert render(s, seed=1).script_hash != base


def test_template_hash_is_stable():
    assert template_hash("pk_2cmt_oral_v1") == template_hash("pk_2cmt_oral_v1")
    assert template_hash("pk_1cmt_oral_v1") != template_hash("pk_2cmt_oral_v1")


def test_rendered_script_reflects_spec(spec):
    s = validate_spec({**spec, "error_model": "proportional", "fixed_params": {"KA": 1.1},
                       "estimation": "SAEM"}).spec
    script = render(s).script
    assert "tka <- fix(log(1.1))" in script
    assert "cp ~ prop(prop.sd)" in script and "add.sd <-" not in script
    assert 'est <- "saem"' in script
    assert "eta.q" not in script  # no IIV on Q
