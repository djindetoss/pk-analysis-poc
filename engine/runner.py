"""PK engine runner: executes a rendered nlmixr2 script with Rscript.

The script receives only file paths. No network access is needed (packages
are pre-installed; the script sets options(repos = NULL)); in the container
the demo runs with `--network none` to prove it.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.core import settings

HERE = Path(__file__).resolve().parent


@dataclass
class EngineOutcome:
    returncode: int
    elapsed_s: float
    results: dict[str, Any] | None
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.results is not None


# glibc's libm selects CPU-specific code paths (AVX2/AVX-512/FMA) at run time, so the same binary can
# round differently on two machines and an ill-conditioned optimisation can stop at a different point.
# Forcing the generic code paths made a FOCEi/M3 fit bit-for-bit identical on 8 GitHub runners with
# 3 CPU models, versus 2 distinct results without it (.github/workflows/reproducibility.yml).
# An explicitly set (even empty) GLIBC_TUNABLES is respected, which is how "native" mode is tested.
PINNED_MATH_ENV = {
    "GLIBC_TUNABLES": "glibc.cpu.hwcaps=-AVX2_Usable,-AVX512F_Usable,-FMA_Usable,-FMA4_Usable,-AVX2,-AVX512F,-FMA,-FMA4",
}


def math_env() -> dict[str, str]:
    """Math-library settings the engine runs with (recorded in each manifest)."""
    if platform.system() != "Linux":
        return {}
    return {k: os.environ.get(k, v) for k, v in PINNED_MATH_ENV.items()}


def engine_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(math_env())
    # macOS with CRAN R binaries but no Fortran toolchain: rxode2's run-time model
    # compilation would try to link -lgfortran, which the generated C does not need.
    if platform.system() == "Darwin" and shutil.which("gfortran") is None:
        env["R_MAKEVARS_USER"] = str(HERE / "Makevars.local")
    return env


def run_engine(script: Path, data: Path, out_dir: Path) -> EngineOutcome:
    cfg = settings()["engine"]
    log_path = out_dir / "engine.log"
    t0 = time.time()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"$ {cfg['rscript']} --vanilla {script.name} {data.name} {out_dir.name}\n")
        log.flush()
        proc = subprocess.run(
            [cfg["rscript"], "--vanilla", str(script), str(data), str(out_dir)],
            stdout=log, stderr=subprocess.STDOUT, env=engine_env(), timeout=cfg["timeout_s"],
        )
    results_path = out_dir / "engine_results.json"
    results = json.loads(results_path.read_text()) if proc.returncode == 0 and results_path.exists() else None
    return EngineOutcome(proc.returncode, round(time.time() - t0, 1), results, log_path)
