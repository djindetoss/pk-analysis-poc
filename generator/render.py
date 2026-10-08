"""Deterministic script generator.

Turns a *validated* spec into an nlmixr2 R script by rendering a versioned
Jinja2 template from the closed catalogue. Same spec + same template version
+ same seed => byte-identical script => identical SHA-256.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jinja2

from common.core import canonical_json, settings, sha256_text
from validator.catalogue import METHODOLOGY_FIELDS, load_catalogue

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
    undefined=jinja2.StrictUndefined,  # a missing variable is an error, never an empty string
    keep_trailing_newline=True,
    autoescape=False,
)


@dataclass(frozen=True)
class RenderedScript:
    template_id: str
    template_version: str
    template_hash: str
    script: str
    script_hash: str
    methodology_hash: str


def _fmt(x: float) -> str:
    return repr(float(x))


def template_hash(template_id: str) -> str:
    """Hash of the template file and every partial it includes."""
    cat = load_catalogue()
    main = cat["templates"][template_id]["file"]
    parts = [main] + sorted(p.name for p in TEMPLATE_DIR.glob("_*.R.j2"))
    return sha256_text("".join((TEMPLATE_DIR / p).read_text() for p in parts))


def methodology(spec: dict[str, Any]) -> dict[str, Any]:
    return {k: spec[k] for k in METHODOLOGY_FIELDS if k in spec}


def render(spec: dict[str, Any], seed: int | None = None) -> RenderedScript:
    cat = load_catalogue()
    tid = spec["template"]
    entry = cat["templates"][tid]
    seed = settings()["analysis"]["seed"] if seed is None else seed
    fixed = spec.get("fixed_params") or {}
    iiv = set(spec["iiv"])

    params = []
    for name in entry["parameters"]:
        r = cat["r_names"][name]
        value = fixed.get(name, entry["initial_estimates"][name])
        params.append({
            "name": name,
            "r": r,
            "theta": f"t{r}",
            "eta": f"eta.{r}",
            "iiv": name in iiv,
            "fixed": name in fixed,
            "init": f"log({_fmt(value)})",
            "label": cat["parameter_labels"][name],
        })

    meth = methodology(spec)
    context = {
        "template_id": tid,
        "template_version": entry["version"],
        "catalogue_version": cat["catalogue_version"],
        "spec_json": canonical_json(meth),
        "seed": int(seed),
        "params": params,
        "compartments": entry["compartments"],
        "error_model": spec["error_model"],
        "estimation": spec["estimation"],
        "blq_method": spec["blq_method"],
        "omega_init": _fmt(cat["omega_initial_variance"]),
        "add_init": _fmt(cat["residual_initial_estimates"]["add_sd"]),
        "prop_init": _fmt(cat["residual_initial_estimates"]["prop_sd"]),
    }
    script = _env.get_template(entry["file"]).render(**context)
    return RenderedScript(
        template_id=tid,
        template_version=entry["version"],
        template_hash=template_hash(tid),
        script=script,
        script_hash=sha256_text(script),
        methodology_hash=sha256_text(canonical_json(meth)),
    )
