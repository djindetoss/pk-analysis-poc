# Demo walkthrough — for a meeting

Two ways to show the tool:

- **Live, from GitHub (recommended in a meeting):** the portal and an issue thread, see [Part A](#part-a--live-demo-from-github).
- **Scripted:** `./run_demo.sh --auto-confirm`, the same scenario in one command; its narration with the reference numbers is in [Part B](#part-b--the-scripted-demo-step-by-step).

Reference numbers come from the CI run of commit `0a6e9da`, image `ghcr.io/djindetoss/pk-analysis-poc:sha-0a6e9da`
(R 4.6.1, nlmixr2 7.0.1, nlmixr2est 7.1.0, rxode2 5.1.7.1, OpenBLAS 0.3.26 with pinned generic kernels),
templates v1.0.3, network disabled. The live issue runs reproduce them exactly.

---

## Part A — Live demo from GitHub

Allow about 2 minutes per bot reply. Prepare a browser tab with the portal and be logged in to GitHub as a
collaborator of the repository.

1. **Portal** – <https://djindetoss.github.io/pk-analysis-poc/>. Show the counters, the "How it works" strip and the request list.
2. **New request** – click the example *Reproduce applicant's model* (the text mentions `XYZ-123`, reported values,
   no BLQ handling), then *Open the request on GitHub* → *Create*.
3. **Clarification** – the bot replies: *"How were concentrations below the LLOQ handled (M1 … M3 …)?"*,
   with what it understood so far. Point out that the identifier reached the language model as `[PRODUCT_1]`,
   and that the missing methodology is asked, not guessed. Reply: `M1, as declared in the report`.
4. **Proposed spec** – a table (template `pk_2cmt_oral_v1`, FOCEi, M1, the six reported values, *primary analysis*).
   Optional: write `Actually use SAEM` to show a correction, then `Sorry, keep FOCE-I`.
5. **`/confirm`** – the run executes in the pinned image with the network off. The bot posts the checks and the
   comparison table (Part B, step 3) with a link to the full report.
6. **`/refine Same model, but handle BLQ with M3.`** → a new proposal labelled **sensitivity analysis – departs
   from the declared methodology on `blq_method`** → `/confirm` → OFV 4397.298.
7. **`/refine Add a time-varying covariate on CL`** → **"⛔ out of scope – expert review"**, no run created.
8. **`/replay`** → script re-rendered with an identical hash, run repeated: **maximum relative difference 0.0**.
   In the end-to-end test (issue #1) the original ran on an *Intel Xeon 8370C* and the replay on an
   *AMD EPYC 7763*, and the results were bit-for-bit identical.
9. **Back to the portal** – the request is listed with its status; *Run history* shows the run tree
   (primary → sensitivity → replay) and the side-by-side comparison.

Issue #1 of the repository is the end-to-end test of this flow and can be shown as a recorded example.

---

## Part B — The scripted demo, step by step

### Step 1 — Synthetic data

`data/make_synthetic.R` (base R only, seed 20240607) writes `data/raw/adpc_synthetic.csv`, read-only:

- 60 subjects (20 rich, 40 sparse), 100 mg oral single dose, 2-compartment model with first-order absorption;
  true values CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h; IIV 30 / 25 / 40% CV on CL / V2 / KA; combined error.
- 420 observation records; LLOQ = 57 ng/mL → 7 BLQ samples = **1.7% overall, 6.2% of late samples** (NTIME ≥ 24 h).
- Injected anomalies: 3 positive concentrations before the dose (SYN-007, SYN-023, SYN-041), one subject (SYN-052) reported in µg/mL.
- SHA-256 of the raw file: `1cb7adff311dcd25…`, identical on the developer's Mac and in the container.

### Step 2 — Run 1, primary analysis

Prompt:

> Reproduce the applicant's model for XYZ-123: two compartments, first-order absorption, combined error, IIV on CL, V2 and KA, FOCE-I. Reported: CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h, IIV on CL 30% CV.

1. **Pseudonymisation:** `XYZ-123` becomes `[PRODUCT_1]` before the extractor sees the text; the mapping stays in the local `pseudonym_map.jsonl`, which is never published.
2. **Extraction:** structure, error model, IIV, FOCEi and the six reported values are filled. **BLQ handling is not stated**, so the harness asks; scripted answer *"M1, as declared in the report"*.
3. **Validation** against the schema and catalogue → template `pk_2cmt_oral_v1` v1.0.3, analysis type `primary`.

### Step 3 — Confirmation, execution, report

- Script SHA-256 `41f519daf7a1adac…`.
- **QC log:** 4 records converted from µg/mL (SYN-052) · 3 pre-dose records excluded · 9 actual times deviating from nominal (warning, kept) · 7 BLQ records discarded (M1). 483 raw records → 473 prepared, **413 observations**. Prepared data SHA-256 `643cf77a70d32f3e…`.
- **Checks:** convergence pass ("Normal exit from bobyqa") · covariance step pass · all RSE < 50% · bounds pass · nlmixr2 diagnostics shown as a warning.

| Parameter | Applicant | Independent (95% CI) | Δ | Flag |
|---|---:|---:|---:|---|
| CL/F (L/h) | 4.2 | 4.09 (3.81–4.39) | −2.6% | – |
| V2/F (L) | 35 | 35.5 (31.5–40.0) | +1.3% | – |
| Q/F (L/h) | 8.1 | 7.94 (7.53–8.38) | −1.9% | – |
| V3/F (L) | 120 | 117.4 (110.4–125.0) | −2.1% | – |
| KA (1/h) | 1.1 | 1.14 (0.96–1.36) | +3.6% | – |
| IIV CL (CV%) | 30 | 27.0 | −10.0% | – |

OFV 4372.933. Residual error: additive 5.8 ng/mL, proportional 14.5%. IIV: CL 27.0%, V2 24.9%, KA 28.7% CV.

**Talking point: the expected M1 bias did not show, and the tool says so.** Only 7 samples (1.7%) are BLQ, so
discarding them barely moves the terminal phase (V3/F −2.1%, Q/F −1.9%); every applicant value lies inside the
95% CI; no flag. Nothing was tuned to produce a difference. To show a visible M1 effect, raise the LLOQ in
`data/make_synthetic.R` (e.g. 20–30% of late samples BLQ).

### Step 4 — Run 2, sensitivity analysis (child of run 1)

*"Same model, but handle BLQ with M3."* → diff `{"blq_method": "M3"}` → re-validated → departure on `blq_method`
→ **sensitivity**. The 7 BLQ records are kept as censored (DV = LLOQ, CENS = 1): 420 observations.
Script `fdffd00a137b56c3…`, prepared data `38d30404d452db32…`.

| Parameter | Applicant | Run 1 (M1) | Run 2 (M3) | Δ run 2 |
|---|---:|---:|---:|---:|
| CL/F (L/h) | 4.2 | 4.09 | 4.14 | −1.4% |
| V2/F (L) | 35 | 35.5 | 35.2 | +0.6% |
| Q/F (L/h) | 8.1 | 7.94 | 7.97 | −1.6% |
| V3/F (L) | 120 | 117.4 | 114.7 | −4.4% |
| KA (1/h) | 1.1 | 1.14 | 1.13 | +3.1% |
| IIV CL (CV%) | 30 | 27.0 | 27.4 | −8.8% |

OFV 4397.298 (not comparable with run 1: the datasets differ). M1 → M3 moves CL/F up (+1.3%) and V3/F down
(−2.3%), the expected direction when low late concentrations become informative again; both runs stay within tolerance.

### Step 5 — Out-of-scope request

*"Add a time-varying covariate on CL"* → listed by the extractor under `unsupported_requests` →
**"out of scope – expert review; unsupported request: time-varying covariate on CL"**. Logged; **no run created**.

### Step 6 — Determinism

- Run 1's script re-rendered from its stored spec: identical hash `41f519daf7a1adac…`; template hash unchanged.
- Run 1 re-executed as a child `replay` run: same script and data hashes, **maximum relative difference 0.0**.

### Step 7 — Summary and run history

The console prints both comparison tables and the path of `runs/index.html` (run tree, side-by-side
comparison, rejected request, determinism checks, audit trail).

---

## Part C — Reproducibility and portability findings

**Across machines, same image.** The first `/replay` on GitHub differed (OFV 4397.205 vs 4397.446) because
OpenBLAS and libm pick CPU-specific code. Measured on 18 runners with 6 CPU models: 2 distinct results when
the libraries are free, 1 when they are pinned. The image now pins them and every manifest records the CPU,
the resolved BLAS/LAPACK and the settings. Details in the README, section *Reproducibility across machines*.

**Across installations.** The same demo also ran outside the container, on a Mac:

| | Mac (R 4.2.2, nlmixr2 2.0.9, R's BLAS) | Container (R 4.6.1, nlmixr2 7.0.1, OpenBLAS pinned) |
|---|---:|---:|
| Raw data hash | `1cb7adff…` | `1cb7adff…` |
| Prepared data hash (run 1) | `643cf77a…` | `643cf77a…` |
| Outer optimiser (nlmixr2 default) | nlminb | bobyqa |
| OFV run 1 | 4372.502 | 4372.933 |
| CL/F run 1 | 4.091 | 4.091 |
| Replay on the same platform | identical (Δ = 0.0) | identical (Δ = 0.0), also across Intel/AMD hosts |

(The Mac run used templates v1.0.0, so its script hash differs; the model is identical.)

Same data, different software stacks: estimates agree to three or four significant digits, OFV to the fifth;
the manifests say exactly which stack produced which number. With the same image and pinned math libraries,
results are identical bit for bit, whatever the host.

## Questions this demo lets you put to a contractor

1. How is the LLM prevented from emitting code, and from silently approximating an unsupported request?
2. What exactly leaves the agency's network, and where is the pseudonym mapping kept?
3. Which manifest fields guarantee that another agency reproduces a run, including the CPU and math-library settings, and how is that tested?
4. How are template changes versioned and validated before use?
5. How does the tool word differences so that it never concludes on the applicant's analysis?
6. Where will the conversation with assessors happen in production (an internal portal, a private GitHub Enterprise instance…), and who may confirm a run?
