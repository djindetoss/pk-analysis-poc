"""Closed template catalogue and the spec schema it is checked against."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "spec_schema_v1.json"
CATALOGUE_PATH = HERE / "catalogue.json"

# Fields that define the analysis methodology. A child run whose methodology
# differs from the declared (primary) one is a sensitivity analysis.
METHODOLOGY_FIELDS = (
    "template", "compartments", "absorption", "route", "error_model",
    "iiv", "estimation", "blq_method", "fixed_params",
)


@lru_cache
def load_catalogue() -> dict[str, Any]:
    return json.loads(CATALOGUE_PATH.read_text())


@lru_cache
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def find_template(compartments: Any, absorption: Any, route: Any) -> str | None:
    for tid, t in load_catalogue()["templates"].items():
        if (t["compartments"], t["absorption"], t["route"]) == (compartments, absorption, route):
            return tid
    return None
