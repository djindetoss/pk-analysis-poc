"""Spec validator: JSON schema + closed catalogue.

Outcomes
--------
valid         maps onto exactly one catalogue template; may be executed after confirmation
incomplete    required fields missing; the harness asks the assessor (never guesses)
out_of_scope  requests a feature or value the catalogue does not cover -> "out of scope - expert review"
invalid       internally inconsistent (e.g. IIV on a parameter the model does not have)

Out-of-scope specs are rejected, never approximated by the nearest template.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from validator.catalogue import METHODOLOGY_FIELDS, find_template, load_catalogue, load_schema

OUT_OF_SCOPE_LABEL = "out of scope – expert review"

# Schema keywords whose violation means "the catalogue cannot express this"
_SCOPE_KEYWORDS = {"additionalProperties", "enum", "propertyNames"}


@dataclass
class ValidationResult:
    status: str
    spec: dict[str, Any] | None = None
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "valid"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "missing": self.missing, "reasons": self.reasons}


def _describe(err: jsonschema.ValidationError) -> str:
    where = "/".join(str(p) for p in err.absolute_path) or "spec"
    if err.validator == "additionalProperties":
        extra = sorted(set(err.instance) - set(err.schema.get("properties", {})))
        return f"unsupported feature(s) requested: {', '.join(extra)}"
    if err.validator == "enum":
        return f"{where} = {err.instance!r} is not in the catalogue (allowed: {err.validator_value})"
    return f"{where}: {err.message}"


def validate_spec(spec: dict[str, Any]) -> ValidationResult:
    spec = copy.deepcopy(spec)
    # Requests the extractor could not express in spec fields: out of scope by definition.
    unsupported = spec.pop("unsupported_features", None)
    if unsupported:
        return ValidationResult("out_of_scope", reasons=[f"unsupported request: {u}" for u in unsupported])
    validator = jsonschema.Draft202012Validator(load_schema())
    errors = sorted(validator.iter_errors(spec), key=lambda e: list(e.absolute_path))

    missing = []
    for e in errors:
        if e.validator == "required":
            missing += [f for f in e.validator_value if f not in e.instance]
    scope = [_describe(e) for e in errors if e.validator in _SCOPE_KEYWORDS]
    other = [_describe(e) for e in errors if e.validator not in _SCOPE_KEYWORDS | {"required"}]

    if scope:
        return ValidationResult("out_of_scope", reasons=scope, missing=sorted(set(missing)))
    if other:
        return ValidationResult("invalid", reasons=other, missing=sorted(set(missing)))

    # Catalogue mapping. Even with fields still missing, reject early if no template
    # can match the structural fields already known (e.g. route = iv).
    structural = ("compartments", "absorption", "route")
    known = {k: spec[k] for k in structural if k in spec}
    if known and not any(all(t[k] == v for k, v in known.items()) for t in load_catalogue()["templates"].values()):
        return ValidationResult("out_of_scope", reasons=[
            "no catalogue template for " + ", ".join(f"{k}={v}" for k, v in known.items())])
    if all(k in spec for k in structural):
        tid = find_template(spec["compartments"], spec["absorption"], spec["route"])
        if tid is None:
            return ValidationResult("out_of_scope", reasons=[
                f"no catalogue template for compartments={spec['compartments']}, "
                f"absorption={spec['absorption']}, route={spec['route']}"])
        if "template" in spec and spec["template"] != tid:
            return ValidationResult("invalid", reasons=[
                f"template {spec['template']!r} does not match the structural fields (expected {tid!r})"])
        spec["template"] = tid

    if missing:
        return ValidationResult("incomplete", spec=spec, missing=sorted(set(missing)))

    # Consistency with the template's parameters
    params = load_catalogue()["templates"][spec["template"]]["parameters"]
    reasons = []
    bad_iiv = [p for p in spec["iiv"] if p not in params]
    if bad_iiv:
        reasons.append(f"IIV requested on {bad_iiv}, not parameters of {spec['template']} ({params})")
    bad_fixed = [p for p in (spec.get("fixed_params") or {}) if p not in params]
    if bad_fixed:
        reasons.append(f"fixed_params {bad_fixed} are not parameters of {spec['template']}")
    ref_params = [k.removeprefix("IIV_") for k in (spec.get("reference_values") or {})]
    bad_ref = sorted({p for p in ref_params if p not in params})
    if bad_ref:
        reasons.append(f"reference values given for {bad_ref}, not parameters of {spec['template']}")
    if reasons:
        return ValidationResult("invalid", reasons=reasons)

    spec["iiv"] = [p for p in params if p in spec["iiv"]]  # canonical order
    spec.setdefault("analysis_type", "primary")
    return ValidationResult("valid", spec=spec)


def methodology_differs(spec: dict[str, Any], declared: dict[str, Any]) -> list[str]:
    """Methodology fields on which `spec` departs from the declared methodology."""
    return [k for k in METHODOLOGY_FIELDS if (spec.get(k) or None) != (declared.get(k) or None)]


def apply_diff(parent: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    """Apply a spec diff: keys overwrite the parent, a None value removes the key."""
    child = copy.deepcopy(parent)
    for k, v in diff.items():
        if v is None:
            child.pop(k, None)
        else:
            child[k] = copy.deepcopy(v)
    return child
