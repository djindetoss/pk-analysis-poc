from validator.validate import validate_spec


def test_valid_spec_maps_to_template(spec):
    v = validate_spec(spec)
    assert v.status == "valid"
    assert v.spec["template"] == "pk_2cmt_oral_v1"
    assert v.spec["analysis_type"] == "primary"


def test_iiv_is_put_in_canonical_order(spec):
    spec["iiv"] = ["KA", "CL", "V2"]
    assert validate_spec(spec).spec["iiv"] == ["CL", "V2", "KA"]


def test_missing_required_field_is_incomplete_not_guessed(spec):
    del spec["blq_method"]
    v = validate_spec(spec)
    assert v.status == "incomplete"
    assert v.missing == ["blq_method"]


def test_wrong_type_is_invalid(spec):
    spec["compartments"] = "two"
    assert validate_spec(spec).status in {"invalid", "out_of_scope"}
    spec["compartments"] = 2
    spec["reference_values"] = {"CL": -1}
    assert validate_spec(spec).status == "invalid"


def test_inconsistent_iiv_is_invalid(spec):
    spec["compartments"] = 1
    spec["reference_values"] = {"CL": 4.2}
    v = validate_spec(spec)  # IIV on V2/KA in a 1-compartment model: V2 does not exist there
    assert v.status == "invalid"
    assert any("V2" in r for r in v.reasons)


def test_template_must_match_structure(spec):
    spec["template"] = "pk_1cmt_oral_v1"
    assert validate_spec(spec).status == "invalid"
