"""Provider-agnostic spec extraction.

    extract_spec(prompt_text, current_spec=None) -> dict

returns an *extraction envelope*:

    {
      "spec": {<spec fields, null when not stated>},   # a full spec, or a diff if current_spec is given
      "unsupported_requests": ["..."],                 # anything the assessor asked that the fields cannot express
      "notes": "..."
    }

The envelope deliberately has an `unsupported_requests` escape hatch: with
constrained decoding, a model without it would be *forced* to squeeze an
out-of-catalogue request (e.g. a time-varying covariate) into the nearest
allowed values, i.e. approximate it silently. The validator turns any
unsupported request into an "out of scope – expert review" rejection.

Extractors only ever receive pseudonymised text and never see any data.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from common.core import settings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PARAMS = ["CL", "V", "V2", "Q", "V3", "KA"]
SPEC_FIELDS = ["compartments", "absorption", "route", "error_model", "iiv", "estimation",
               "blq_method", "fixed_params", "reference_values", "analysis_type"]


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def envelope_schema() -> dict[str, Any]:
    """JSON schema of the envelope, used for structured outputs and for re-validation.

    Structured outputs allow at most 16 union-typed (nullable) parameters, so the
    parameter/value maps are expressed as lists of {"param", "value"} pairs
    (an empty list means "not stated") instead of objects with one nullable key each.
    """
    ref_keys = PARAMS + [f"IIV_{p}" for p in PARAMS]

    def pairs(keys: list[str]) -> dict:
        return {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["param", "value"],
            "properties": {"param": {"type": "string", "enum": keys}, "value": {"type": "number"}}}}

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["spec", "unsupported_requests", "notes"],
        "properties": {
            "spec": {
                "type": "object",
                "additionalProperties": False,
                "required": SPEC_FIELDS,
                "properties": {
                    "compartments": _nullable({"type": "integer", "enum": [1, 2]}),
                    "absorption": _nullable({"type": "string", "enum": ["first_order"]}),
                    "route": _nullable({"type": "string", "enum": ["oral", "iv"]}),
                    "error_model": _nullable({"type": "string", "enum": ["additive", "proportional", "combined"]}),
                    "iiv": _nullable({"type": "array", "items": {"type": "string", "enum": PARAMS}}),
                    "estimation": _nullable({"type": "string", "enum": ["FOCEi", "SAEM"]}),
                    "blq_method": _nullable({"type": "string", "enum": ["M1", "M3"]}),
                    "fixed_params": pairs(PARAMS),
                    "reference_values": pairs(ref_keys),
                    "analysis_type": _nullable({"type": "string", "enum": ["primary", "sensitivity"]}),
                },
            },
            "unsupported_requests": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        },
    }


def pairs_to_dict(pairs: list[dict[str, Any]] | None) -> dict[str, float] | None:
    return {p["param"]: float(p["value"]) for p in pairs} if pairs else None


def empty_envelope() -> dict[str, Any]:
    spec = {k: None for k in SPEC_FIELDS}
    spec["fixed_params"], spec["reference_values"] = [], []
    return {"spec": spec, "unsupported_requests": [], "notes": ""}


class Extractor(ABC):
    name: str = "abstract"
    model: str = "n/a"
    prompt_version: str = "n/a"

    @abstractmethod
    def extract_spec(self, prompt_text: str, current_spec: dict | None = None) -> dict[str, Any]:
        ...


# --------------------------------------------------------------------------
# Mock extractor: deterministic rules, no network, no API key
# --------------------------------------------------------------------------
_NUM = r"(\d+(?:\.\d+)?)"
_OUT_OF_SCOPE = [
    (r"time[- ]varying", "time-varying covariate"),
    (r"\bcovariates?\b", "covariate model"),
    (r"\bTMDD\b|target[- ]mediated", "target-mediated drug disposition"),
    (r"\bmixture\b", "mixture model"),
    (r"\btransit\b", "transit-compartment absorption"),
    (r"zero[- ]order|lag[- ]?time|\btlag\b", "absorption model other than first order without lag"),
    (r"michaelis|saturable|non[- ]?linear elimination", "non-linear elimination"),
    (r"\b(three|3)[- ]compartments?\b|\b3[- ]?cmt\b", "three-compartment model"),
    (r"\bIOV\b|inter[- ]occasion", "inter-occasion variability"),
    (r"\bLaplacian\b|\bimportance sampling\b|\bBayesian\b", "estimation method outside FOCEi/SAEM"),
]


class MockExtractor(Extractor):
    """Rule-based parser for demo prompts. Deterministic: same text -> same envelope."""
    name = "mock"
    model = "rule-based"
    prompt_version = "mock_v1"

    def extract_spec(self, prompt_text: str, current_spec: dict | None = None) -> dict[str, Any]:
        t = prompt_text
        env = empty_envelope()
        s = env["spec"]

        for pat, label in _OUT_OF_SCOPE:
            m = re.search(pat, t, re.IGNORECASE)
            if m:
                where = re.search(r"\bon\s+(CL|V2|V3|V|Q|KA)\b", t[m.start():], re.IGNORECASE)
                env["unsupported_requests"].append(label + (f" on {where.group(1).upper()}" if where else ""))
                if label == "time-varying covariate":
                    break  # avoid double-reporting it as a generic covariate model

        if re.search(r"\b(two|2)[- ]compartments?\b|\b2[- ]?cmt\b|\bbi[- ]?compartmental\b", t, re.I):
            s["compartments"] = 2
        elif re.search(r"\b(one|1)[- ]compartments?\b|\b1[- ]?cmt\b|\bmono[- ]?compartmental\b", t, re.I):
            s["compartments"] = 1
        if re.search(r"first[- ]order absorption", t, re.I):
            s["absorption"] = "first_order"
        if re.search(r"\b(iv|intravenous|infusion|bolus)\b", t, re.I):
            s["route"] = "iv"
        elif re.search(r"\b(oral|tablet|capsule|po)\b|absorption", t, re.I):
            s["route"] = "oral"
        if re.search(r"combined|additive\s*(\+|and|plus)\s*proportional|proportional\s*(\+|and|plus)\s*additive", t, re.I):
            s["error_model"] = "combined"
        elif re.search(r"proportional", t, re.I):
            s["error_model"] = "proportional"
        elif re.search(r"additive", t, re.I):
            s["error_model"] = "additive"
        if re.search(r"FOCE[- ]?I|FOCEi|FOCE with interaction", t, re.I):
            s["estimation"] = "FOCEi"
        elif re.search(r"\bSAEM\b", t, re.I):
            s["estimation"] = "SAEM"

        # BLQ: explicit method names first, then common paraphrases
        if re.search(r"\bM3\b|likelihood[- ]based|censor", t, re.I):
            s["blq_method"] = "M3"
        elif re.search(r"\bM1\b|discard|drop|exclud|ignor|remov", t, re.I) and re.search(r"BLQ|M1|below", t, re.I):
            s["blq_method"] = "M1"
        elif re.fullmatch(r"\s*M1\b.*", t, re.I | re.S):
            s["blq_method"] = "M1"

        # IIV: "IIV on CL, V2 and KA" (structure clause, without numbers)
        iiv: list[str] = []
        for m in re.finditer(r"(?:IIV|BSV|inter[- ]individual variability|random effects?)\s+on\s+([^.;:]*)", t, re.I):
            clause = re.split(r"(?<![A-Za-z])\d", m.group(1))[0]  # stop at a value such as "30% CV"
            iiv += [p.upper() for p in re.findall(r"\b(CL|V2|V3|V|Q|KA)\b", clause, re.I)]
        if iiv:
            s["iiv"] = list(dict.fromkeys(iiv))

        # Reported values: "CL/F 4.2 L/h", "V2/F = 35 L", "KA 1.1 /h", "IIV on CL 30% CV"
        ref: dict[str, float] = {}
        for p in ["CL", "V2", "V3", "Q", "KA", "V"]:
            m = re.search(rf"\b{p}(?:/F)?\s*(?:=|:|of)?\s*{_NUM}\s*(?:L/h|L|/h|1/h|h-1|h⁻¹)", t, re.I)
            if m:
                ref[p] = float(m.group(1))
        for m in re.finditer(rf"(?:IIV|BSV)\s+on\s+(CL|V2|V3|V|Q|KA)\s*(?:of|=|:)?\s*{_NUM}\s*%", t, re.I):
            ref[f"IIV_{m.group(1).upper()}"] = float(m.group(2))
        if ref:
            s["reference_values"] = [{"param": k, "value": v} for k, v in ref.items()]

        fixed: dict[str, float] = {}
        for m in re.finditer(rf"\bfix(?:ed)?\s+(CL|V2|V3|V|Q|KA)\s*(?:at|to|=)\s*{_NUM}", t, re.I):
            fixed[m.group(1).upper()] = float(m.group(2))
        if fixed:
            s["fixed_params"] = [{"param": k, "value": v} for k, v in fixed.items()]

        if current_spec is None and re.search(r"reproduc|applicant'?s model|as declared", t, re.I):
            s["analysis_type"] = "primary"

        env["notes"] = "rule-based extraction"
        return env


# --------------------------------------------------------------------------
# Anthropic extractor: used only when ANTHROPIC_API_KEY is set
# --------------------------------------------------------------------------
class ExtractionFailed(RuntimeError):
    pass


class AnthropicExtractor(Extractor):
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic  # imported lazily: not needed for the mock path

        cfg = settings()["llm"]
        self.model = cfg["anthropic_model"]
        self.prompt_version = cfg["prompt_version"]
        self.system_prompt = (PROMPTS_DIR / f"{self.prompt_version}.md").read_text()
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()

    def extract_spec(self, prompt_text: str, current_spec: dict | None = None) -> dict[str, Any]:
        if current_spec is None:
            user = f"<assessor_request>\n{prompt_text}\n</assessor_request>\n\nMode: NEW SPEC."
        else:
            user = (f"<current_spec>\n{json.dumps(current_spec, sort_keys=True)}\n</current_spec>\n"
                    f"<assessor_request>\n{prompt_text}\n</assessor_request>\n\n"
                    "Mode: DIFF. Return only the fields the request changes; every other field null.")
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            # Sampling parameters (temperature) are not accepted by current Claude models;
            # low effort + a schema-constrained output keep extraction stable.
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": envelope_schema()}},
            # If the model declines, the API re-runs the request on a fallback model;
            # the model that actually answered is recorded in the call log.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=self.system_prompt,
            messages=[{"role": "user", "content": user}],
        )
        self.last_served_by = response.model
        if response.stop_reason == "refusal":
            raise ExtractionFailed(f"model declined the request ({response.stop_details})")
        if response.stop_reason == "max_tokens":
            raise ExtractionFailed("model output truncated")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)


def get_extractor(name: str | None = None) -> Extractor:
    """Anthropic if an API key is configured (or explicitly requested), else the mock."""
    name = name or os.environ.get("PKPOC_EXTRACTOR") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "mock")
    if name == "anthropic":
        return AnthropicExtractor()
    return MockExtractor()
