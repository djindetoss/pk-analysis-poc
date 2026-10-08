import copy

import pytest

BASE_SPEC = {
    "compartments": 2, "absorption": "first_order", "route": "oral", "error_model": "combined",
    "iiv": ["CL", "V2", "KA"], "estimation": "FOCEi", "blq_method": "M1",
    "reference_values": {"CL": 4.2, "V2": 35.0, "Q": 8.1, "V3": 120.0, "KA": 1.1, "IIV_CL": 30.0},
}


@pytest.fixture(autouse=True)
def isolated_run_store(tmp_path, monkeypatch):
    """Every test gets its own empty run store; nothing touches ./runs."""
    monkeypatch.setenv("PKPOC_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PKPOC_EXTRACTOR", raising=False)
    return tmp_path / "runs"


@pytest.fixture
def spec():
    return copy.deepcopy(BASE_SPEC)
