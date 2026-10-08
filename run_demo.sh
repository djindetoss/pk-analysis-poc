#!/usr/bin/env bash
# One-command end-to-end demo. Usage: ./run_demo.sh [--auto-confirm] [--skip-replay]
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
[ -x .venv/bin/python ] && [ -z "${PYTHON:-}" ] && PY=.venv/bin/python

echo "== STEP 1: synthetic data =="
Rscript --vanilla data/make_synthetic.R data/raw/adpc_synthetic.csv
"$PY" -m orchestrator.demo "$@"
