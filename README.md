# pk-analysis-poc — AI-assisted independent population-PK analysis (demonstrator)

[![CI](https://github.com/djindetoss/pk-analysis-poc/actions/workflows/ci.yml/badge.svg)](https://github.com/djindetoss/pk-analysis-poc/actions/workflows/ci.yml)
[![Image](https://img.shields.io/github/v/tag/djindetoss/pk-analysis-poc?label=ghcr.io%20image&logo=docker)](https://github.com/djindetoss/pk-analysis-poc/pkgs/container/pk-analysis-poc)
[![Demo](https://img.shields.io/badge/demo-GitHub%20Pages-1f5f8b)](https://djindetoss.github.io/pk-analysis-poc/)
[![Licence: EUPL-1.2](https://img.shields.io/badge/licence-EUPL--1.2-blue)](LICENSE)

End-to-end simulation of a tool that lets a regulatory assessor run an **independent population-PK
analysis** and compare it with the applicant's submitted results. Built for IncreaseNET Task 7.7
(portability of AI tools across European medicines agencies) to demonstrate the target architecture
to technical colleagues and to challenge a contractor's proposals.

> **Demonstrator, not production software.** Synthetic data only: no real data, product names or
> credentials appear anywhere in the repository, the image, the logs or the published pages.

**Browse the latest demo run:** <https://djindetoss.github.io/pk-analysis-poc/> (rebuilt by CI on every push to `main`).
A narrated walkthrough with the numbers obtained is in [DEMO_WALKTHROUGH.md](DEMO_WALKTHROUGH.md).

---

## Architecture

```
 assessor (CLI)
     │  free text: "Reproduce the applicant's model for XYZ-123: two compartments, ..."
     ▼
 ┌──────────────┐   pseudonymised text only    ┌───────────────────────────────────────┐
 │ orchestrator │ ───────────────────────────► │ LLM harness                           │
 │  pipeline.py │ ◄─────────────────────────── │  pseudonymise → extract (≤2 retries)  │
 └──────┬───────┘   spec / spec diff /         │  → log → clarification questions      │
        │           clarification questions    │  MockExtractor | AnthropicExtractor   │
        ▼                                      └───────────────────────────────────────┘
 ┌──────────────────────────┐
 │ spec validator           │── out of scope ──► STOP: "out of scope – expert review" (logged, no run)
 │  JSON schema + catalogue │── incomplete ───► question back to the assessor (never guessed)
 └──────┬───────────────────┘
        ▼  assessor sees and confirms the spec   (child run departs from declared methodology → "sensitivity")
 ┌──────────────────────────┐
 │ script generator         │  versioned Jinja2 template → script.R, SHA-256, run ID
 └──────┬───────────────────┘
        ▼
 ┌──────────────────────────┐     raw data (read-only) ── never leaves this box ──┐
 │ data QC & preparation    │◄────────────────────────────────────────────────────┘
 │  every exclusion/fix logged, prepared-data SHA-256
 └──────┬───────────────────┘
        ▼
 ┌──────────────────────────┐
 │ PK engine (R, nlmixr2)   │  Rscript, fixed seed, single thread, no network, fit.rds checkpoint
 └──────┬───────────────────┘
        ▼
 ┌──────────────────────────┐
 │ checks & outputs         │  convergence, covariance, RSE, bounds → comparison table (flags only)
 │                          │  → GOF plots, report.html, results.json, comparison.csv
 └──────┬───────────────────┘
        ▼
 audit log (append-only)  +  run manifests  +  run-history page (run tree, side-by-side runs)
```

| Module | Path | Role |
|---|---|---|
| Orchestrator | [`orchestrator/`](orchestrator) | Pipeline, CLI (`typer`), run store, manifests, demo driver |
| LLM harness | [`harness/`](harness) | Pseudonymisation, extractors, call log, clarification, eval bench |
| Spec validator | [`validator/`](validator) | [`spec_schema_v1.json`](validator/spec_schema_v1.json), [`catalogue.json`](validator/catalogue.json), spec diffs |
| Script generator | [`generator/`](generator) | Jinja2 templates [`pk_1cmt_oral_v1`](generator/templates/pk_1cmt_oral_v1.R.j2), [`pk_2cmt_oral_v1`](generator/templates/pk_2cmt_oral_v1.R.j2) |
| Data QC | [`qc/`](qc) | Checks, harmonisation, BLQ handling, dataset hash |
| PK engine | [`engine/`](engine) | `Rscript` runner (nlmixr2 / rxode2) |
| Reporting | [`reporting/`](reporting) | Checks, comparison, HTML report, run history, static site |
| Synthetic data | [`data/make_synthetic.R`](data/make_synthetic.R) | 60-subject ADPC-like dataset with injected anomalies |
| Tests | [`tests/`](tests) | `pytest` suite |

## How each design rule is enforced in code

| Rule | Enforcement |
|---|---|
| **1. The LLM never writes code** | The extractor interface returns a JSON *envelope* validated against a schema ([`harness/extractors.py`](harness/extractors.py)); with Claude, the response is constrained by structured outputs to that schema. R code exists only in versioned templates rendered with `StrictUndefined` ([`generator/render.py`](generator/render.py)); the only values interpolated are enumerations and numbers from the validated spec and the catalogue. |
| **2. No data to the LLM** | The harness is the only caller of an extractor and only passes the pseudonymised prompt (and, for a refinement, the current spec). Data are read only by [`qc/qc.py`](qc/qc.py) and the R script, which receives file paths. The raw file is written read-only (`chmod 444`). A test spies on the text reaching the extractor and on the LLM log ([`tests/test_pseudonymisation.py`](tests/test_pseudonymisation.py)). |
| **3. Closed catalogue** | [`validator/validate.py`](validator/validate.py): unknown keys or values outside the enumerations are classified *out of scope*, as is any structure no template matches (e.g. `route = iv`). The extraction envelope has an `unsupported_requests` field so that a constrained LLM is never *forced* to squeeze, say, a time-varying covariate into the nearest allowed value; any entry there is a rejection. Rejections are logged and no run is created. |
| **4. Full traceability** | Every run gets an ID and a [`manifest.json`](orchestrator/runs.py) (template id/version/hash, script hash, raw and prepared data hashes, seed, R and Python package versions, parent run ID, analysis type, extractor/model/prompt version, confirmation, settings, hashes of every output). Run directories are created with `exist_ok=False` and frozen read-only on completion; the audit log is append-only. Nothing is deleted. |
| **5. Human control** | `submit()` always calls a `confirm(spec, analysis_type, departures)` callback before generating or executing anything; `--auto-confirm` exists only for the demo and is recorded as such in the manifest. A child run whose methodology fields differ from the root primary run is labelled `sensitivity`, with the differing fields listed. |
| **6. The tool flags, it does not conclude** | [`reporting/compare.py`](reporting/compare.py) reports relative differences, a flag above tolerance, and *generic* possible sources of difference plus factual run context; a non-converged run is "not comparable". A test checks the report contains no accusatory wording. |

## Running the demo

### With a container (recommended, identical everywhere)

```bash
podman pull ghcr.io/djindetoss/pk-analysis-poc:latest
```

```bash
mkdir -p runs && podman run --rm --network none -v "$PWD/runs:/app/runs" ghcr.io/djindetoss/pk-analysis-poc:latest
```

Then open `runs/index.html`. Docker works the same way (`docker run ...`). To build the image yourself:
`podman build -t pk-analysis-poc -f Containerfile .` (the first build installs nlmixr2 from binary packages, ~10–15 min).

### Without containers

Requirements: R ≥ 4.2 with `nlmixr2`, `ggplot2`, `jsonlite` (see [`install_r_packages.R`](install_r_packages.R)), Python ≥ 3.11.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
./run_demo.sh --auto-confirm
```

On macOS with the CRAN R binaries but no Fortran toolchain, rxode2's run-time model compilation tries to
link `-lgfortran`; the engine then points `R_MAKEVARS_USER` to [`engine/Makevars.local`](engine/Makevars.local)
(the generated C code needs no Fortran runtime). Your global R configuration is not modified.

### In the browser (Codespaces)

Open the repository in a Codespace: the [devcontainer](.devcontainer/devcontainer.json) uses the published
image, so `./run_demo.sh --auto-confirm` works immediately.

### Using the CLI interactively

```bash
.venv/bin/python -m orchestrator.cli submit "Reproduce the applicant's model for XYZ-123: two compartments, first-order absorption, combined error, IIV on CL, V2 and KA, FOCE-I."
```

The harness asks the missing questions (BLQ handling here), shows the spec, and waits for confirmation.
Other commands: `refine <run_id> "<text>"`, `rerender <run_id>`, `replay <run_id>`, `history`.

## The demo scenario (`./run_demo.sh`)

1. Generate synthetic data (60 subjects, 100 mg oral, 2-cmt; LLOQ set so ~6% of late samples are BLQ; 3 pre-dose concentrations and one subject in µg/mL injected).
2. **Run 1 (primary).** The harness pseudonymises `XYZ-123`, extracts the spec, notices that BLQ handling is missing and asks; the scripted answer is "M1, as declared in the report".
3. The spec is shown and auto-confirmed; the run executes and produces its report.
4. **Run 2 (sensitivity, child of run 1).** "Same model, but handle BLQ with M3" → diff `{"blq_method": "M3"}` → re-validated → labelled sensitivity.
5. **Out of scope.** "Add a time-varying covariate on CL" → rejected and logged, no run.
6. **Determinism.** Run 1's script is re-rendered from its stored spec (identical hash) and run 1 is re-executed as a child `replay` run (identical estimates within 1e-6).
7. Console summary and path to the run-history page.

## LLM extraction

- `extract_spec(prompt_text, current_spec=None) -> dict` ([`harness/extractors.py`](harness/extractors.py)).
- **`MockExtractor`** (default): deterministic rule-based parser of the demo prompts; no network, no key.
- **`AnthropicExtractor`**: used when `ANTHROPIC_API_KEY` is set (from a local `.env` that is never committed, or the GitHub Actions secret). Versioned system prompt [`harness/prompts/system_v1.md`](harness/prompts/system_v1.md), output constrained to the envelope schema with structured outputs, re-validated, at most 2 retries; after that the harness returns the list of fields for the assessor to supply instead of guessing.
- Every call is logged in `runs/audit/llm_calls.jsonl` (job ID, prompt hash, pseudonymised prompt, response, model, prompt version, attempts). The token → identifier mapping is written only to `runs/audit/pseudonym_map.jsonl`, which is excluded from the published site.
- **Evaluation bench:** [`harness/eval/cases.jsonl`](harness/eval/cases.jsonl) (11 prompts with expected fields, including "must stay null" and out-of-scope cases).

```bash
.venv/bin/python -m harness.eval.run_eval --extractor mock
```

  The mock scores 100% by construction (its rules were written for these prompts); the bench is meant for the real LLM (`--extractor anthropic`, run automatically in CI when the secret exists).

**Deviation from the brief — temperature 0.** Current Claude models reject sampling parameters
(`temperature`) with a 400 error, so the extractor cannot set it. Stability comes instead from a
schema-constrained output, low effort, a fixed prompt version, and above all from the architecture:
the LLM output is only a proposal that the assessor confirms, the confirmed spec is stored, and every
replay or re-render uses the stored spec — the LLM is never called again to reproduce a run.

## BLQ handling and the nlmixr2 syntax used

- **M1**: QC removes BLQ records before estimation (count logged).
- **M3**: QC keeps BLQ records with `DV = LLOQ` and `CENS = 1`. In nlmixr2, `CENS = 1` means the observation is
  left-censored with `DV` as the upper limit, and FOCEi then uses the likelihood contribution P(Y < LLOQ)
  (Beal's M3). A `LIMIT` column is not needed for M3 (it would set the lower bound, i.e. M4). The run summary
  confirms it (`fit$censInformation`, and `n_censored` in `engine_results.json`). Checked against the nlmixr2
  documentation on censoring and on nlmixr2 2.0.9 (local) and the version pinned in the image.

## Traceability and reproducibility

Each run directory `runs/<run_id>/` contains: `spec.json`, `script.R`, `prepared_data.csv`, `qc_log.json`,
`engine.log`, `fit.rds` (checkpoint: if a run crashes after estimation, re-running the script resumes at
post-processing), `engine_results.json`, `individual_parameters.csv`, GOF PNGs, `results.json`,
`comparison.csv`, `report.html` and `manifest.json`. The engine runs single-threaded with a fixed seed so
that a replay reproduces the estimates bit for bit on the same image.

## Portability: how another agency redeploys it

1. Pull exactly the same image: `ghcr.io/djindetoss/pk-analysis-poc:<version>` or `:sha-<commit>` (the image tag is recorded in each manifest under `environment.container_image`).
2. Run the demo (or a stored spec) with `--network none`.
3. Compare manifests: identical `template.hash`, `script_hash`, `input_data.prepared_hash`, `packages.r` → the estimates must be identical. **Same container + same manifest → same results.** A difference in any of those fields explains *why* numbers can differ (e.g. a local R 4.2 / nlmixr2 2.0.9 installation versus the image's pinned versions).

The R stack is pinned by the base image tag (`rocker/r-ver:4.6.1` freezes CRAN at a dated Posit Package
Manager snapshot, which also provides binary packages), Python packages are pinned in `requirements.txt`.

## Continuous integration and deployment

[`.github/workflows/ci.yml`](.github/workflows/ci.yml), on every push and pull request:
build the image (R layer cached in the GitHub Actions cache) → `pytest` → evaluation bench (mock) →
full demo **with the network disabled** → static site. On `main`: push the image to GHCR tagged
`sha-<commit>`, `<version>` and `latest`, and deploy the site to GitHub Pages. The optional
`ANTHROPIC_API_KEY` secret only enables an extra job evaluating the real LLM; CI passes without it.

## Licence

**EUPL-1.2.** The tool's own code is independent of nlmixr2 (which it calls as a separate `Rscript`
process), but the generated R scripts load nlmixr2, which is GPL (≥ 2). The EUPL is the European
Commission's licence for public-sector software, is available in all EU languages, and its
compatibility clause explicitly lists GPL-2.0 and GPL-3.0, so a combined work can be distributed under
the GPL when required. That makes it a natural fit for a tool meant to be shared between European agencies.
The container image bundles R packages under their own licences (mostly GPL).

## Assumptions, limitations, risks

- Two templates only, by design; anything else is out of scope. Initial estimates come from the catalogue, not from the applicant's values, so that the analysis stays independent.
- `linCmt()` (analytical solution) is used for speed; parameters are named `cl, vc, q, vp, ka` internally and mapped to `CL, V2, Q, V3, KA` in outputs to avoid nlmixr2's ambiguous `V2/V3` naming.
- RSE is reported on the natural scale as 100 × SE(log θ); nlmixr2's own `%RSE` column refers to the log-scale estimate.
- `xpose.nlmixr2` was not used: GOF plots are drawn with `ggplot2` directly to keep the image smaller and the dependency surface minimal.
- The local Mac used for development ran R 4.2.2 / nlmixr2 2.0.9; the image runs R 4.6.1 with the snapshot's nlmixr2. Numbers can differ slightly across the two; the manifests say which is which.
- The mock extractor is a demonstration device, not an NLP component.
