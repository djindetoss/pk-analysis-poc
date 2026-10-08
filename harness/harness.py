"""LLM harness: pseudonymisation -> extraction (with retries) -> logging -> clarification.

The harness is the only path to a language model. It receives assessor text,
never data, and returns a spec (or spec diff) for the validator.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from common.core import append_jsonl, audit_dir, now_iso, settings, sha256_text
from harness.extractors import Extractor, envelope_schema, get_extractor
from harness.pseudonymise import pseudonymise, store_mapping
from validator.catalogue import load_schema

QUESTIONS = {
    "compartments": "How many compartments does the applicant's structural model have (1 or 2)?",
    "absorption": "Which absorption model was used (only first-order absorption is available)?",
    "route": "What is the route of administration (oral or IV)?",
    "error_model": "Which residual error model was used (additive, proportional or combined)?",
    "iiv": "On which parameters was inter-individual variability estimated (CL, V/V2, Q, V3, KA)?",
    "estimation": "Which estimation method should be used (FOCEi or SAEM)?",
    "blq_method": "How were concentrations below the LLOQ handled (M1: discarded, M3: likelihood-based censoring)?",
}


@dataclass
class HarnessResult:
    job_id: str
    status: str  # ok | needs_clarification | failed
    spec: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    pseudonymised_prompt: str = ""
    error: str = ""


def _strip_nulls(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if vv is not None} or None
        if v is not None:
            out[k] = v
    return out


def envelope_to_spec(envelope: dict[str, Any]) -> dict[str, Any]:
    spec = _strip_nulls(envelope["spec"])
    if envelope.get("unsupported_requests"):
        # Not a schema field: the validator rejects it as out of scope.
        spec["unsupported_features"] = list(envelope["unsupported_requests"])
    return spec


def interpret(prompt_text: str, current_spec: dict | None = None,
              extractor: Extractor | None = None, job_id: str | None = None) -> HarnessResult:
    extractor = extractor or get_extractor()
    job_id = job_id or f"J-{uuid.uuid4().hex[:10]}"
    ps = pseudonymise(prompt_text)
    store_mapping(job_id, ps.mapping)  # local audit store only

    max_retries = settings()["llm"]["max_retries"]
    validator = jsonschema.Draft202012Validator(envelope_schema())
    envelope, error, attempts = None, "", 0
    for attempt in range(1 + max_retries):
        attempts = attempt + 1
        try:
            candidate = extractor.extract_spec(ps.text, current_spec)
            errs = list(validator.iter_errors(candidate))
            if errs:
                error = f"envelope schema violation: {errs[0].message}"
                continue
            envelope, error = candidate, ""
            break
        except Exception as exc:  # logged and retried; never guessed around
            error = f"{type(exc).__name__}: {exc}"

    append_jsonl(audit_dir() / "llm_calls.jsonl", {
        "ts": now_iso(),
        "job_id": job_id,
        "extractor": extractor.name,
        "model": extractor.model,
        "prompt_version": extractor.prompt_version,
        "mode": "diff" if current_spec is not None else "new",
        "prompt_hash": sha256_text(ps.text),
        "pseudonymised_prompt": ps.text,
        "n_identifiers_replaced": len(ps.mapping),
        "attempts": attempts,
        "response": envelope,
        "error": error,
    })

    if envelope is None:
        # After the retries: hand the assessor the list of fields to supply, do not guess.
        return HarnessResult(job_id, "failed", missing=list(QUESTIONS), questions=list(QUESTIONS.values()),
                             pseudonymised_prompt=ps.text, error=error)

    spec = envelope_to_spec(envelope)
    result = HarnessResult(job_id, "ok", spec=spec, unsupported=list(envelope.get("unsupported_requests", [])),
                           pseudonymised_prompt=ps.text)
    if current_spec is None and not result.unsupported:
        required = load_schema()["required"]
        result.missing = [f for f in required if f not in spec]
        if result.missing:
            result.status = "needs_clarification"
            result.questions = [QUESTIONS[f] for f in result.missing]
    return result
