# Demo walkthrough — for a meeting

This narrates one execution of `./run_demo.sh --auto-confirm`. The numbers below are those of the
reference execution in GitHub Actions, inside the published image
`ghcr.io/djindetoss/pk-analysis-poc:sha-cb41dae` (R 4.6.1, nlmixr2 7.0.1, nlmixr2est 7.1.0,
rxode2 5.1.7.1), run with `--network none`. The resulting reports are browsable at
<https://djindetoss.github.io/pk-analysis-poc/> (the site is rebuilt on every push, so run IDs there
will be more recent; the numbers are identical as long as the image and templates do not change).

Total duration of the demo in CI: about one minute of estimation (≈ 15 s per FOCEi fit).

---

## Step 1 — Synthetic data

`data/make_synthetic.R` (base R only, seed 20240607) writes `data/raw/adpc_synthetic.csv`, read-only:

- 60 subjects (20 rich, 40 sparse), 100 mg oral single dose, 2-compartment model with first-order absorption;
  true values CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h; IIV 30 / 25 / 40% CV on CL / V2 / KA; combined error.
- 420 observation records; LLOQ = 57 ng/mL → 7 BLQ samples = **1.7% overall, 6.2% of late samples** (NTIME ≥ 24 h).
- Injected anomalies: 3 positive concentrations before the dose (SYN-007, SYN-023, SYN-041), one subject (SYN-052) reported in µg/mL.
- SHA-256 of the raw file: `1cb7adff311dcd25…` — identical on the developer's Mac and in the container.

## Step 2 — Run 1, primary analysis

Assessor prompt:

> Reproduce the applicant's model for XYZ-123: two compartments, first-order absorption, combined error, IIV on CL, V2 and KA, FOCE-I. Reported: CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h, IIV on CL 30% CV.

What happens:

1. **Pseudonymisation.** `XYZ-123` becomes `[PRODUCT_1]` before the extractor sees the text; the mapping goes only to the local `pseudonym_map.jsonl` (not published).
2. **Extraction** (mock extractor here): structure, error model, IIV, FOCEi and the six reported values are filled. **BLQ handling is not stated**, so the field stays empty and the harness asks:
   *"How were concentrations below the LLOQ handled (M1: discarded, M3: likelihood-based censoring)?"*
   Scripted assessor answer: *"M1, as declared in the report"* → diff `{"blq_method": "M1"}`.
3. **Validation** against the schema and catalogue → template `pk_2cmt_oral_v1`, analysis type `primary`.

## Step 3 — Confirmation, execution, report

The spec is printed for the assessor and confirmed (`--auto-confirm`, recorded as such in the manifest).

- Script rendered from the template: SHA-256 `f4123a0e77a2b3bd…`.
- **QC log:** units converted for SYN-052 (4 records, fix) · 3 pre-dose records excluded · 9 records with actual time deviating from nominal (warning, kept) · 7 BLQ records discarded (M1). 483 raw records → 473 prepared, **413 observations** used. Prepared dataset SHA-256 `643cf77a70d32f3e…`.
- **Checks:** convergence pass ("Normal exit from bobyqa") · covariance step pass · all RSE < 50% · bounds pass · nlmixr2 diagnostic messages shown as a warning ("gradient problems with covariance…").

**Comparison with the applicant (tolerance 20%):**

| Parameter | Applicant | Independent (95% CI) | Δ | Flag |
|---|---:|---:|---:|---|
| CL/F (L/h) | 4.2 | 4.09 (3.81–4.39) | −2.6% | – |
| V2/F (L) | 35 | 35.4 (31.4–40.0) | +1.2% | – |
| Q/F (L/h) | 8.1 | 7.96 (7.54–8.40) | −1.8% | – |
| V3/F (L) | 120 | 117.4 (110.4–125.0) | −2.1% | – |
| KA (1/h) | 1.1 | 1.14 (0.96–1.36) | +3.7% | – |
| IIV CL (CV%) | 30 | 26.6 | −11.4% | – |

OFV 4372.519. Residual error: additive 2.63 ng/mL, proportional 14.6%. IIV: CL 26.6%, V2 24.9%, KA 29.4% CV.

**Talking point — the expected M1 bias did not materialise, and the tool says so.** The brief expected V3/F
and Q/F to deviate under M1. With this dataset only 7 samples (1.7%) are BLQ, so discarding them barely
moves the terminal phase: V3/F −2.1%, Q/F −1.8%, all applicant values inside the 95% CIs, no flag. The
result is reported as obtained; nothing was tuned to produce a difference. (To show a visible M1 effect,
regenerate the data with a higher LLOQ — e.g. 20–30% of late samples BLQ — which is a one-line change in
`data/make_synthetic.R`.)

## Step 4 — Run 2, sensitivity analysis (child of run 1)

Prompt: *"Same model, but handle BLQ with M3."* → diff `{"blq_method": "M3"}` → applied to run 1's spec →
re-validated → the orchestrator detects a departure from the declared methodology on `blq_method` and labels
the run **sensitivity**. QC now keeps the 7 BLQ records as censored (DV = LLOQ, CENS = 1): 420 observations.
New script hash `252de1359468ef60…`, new prepared-data hash `38d30404d452db32…`.

| Parameter | Applicant | Run 1 (M1) | Run 2 (M3) | Δ run 2 |
|---|---:|---:|---:|---:|
| CL/F (L/h) | 4.2 | 4.09 | 4.14 | −1.4% |
| V2/F (L) | 35 | 35.4 | 35.2 | +0.6% |
| Q/F (L/h) | 8.1 | 7.96 | 7.97 | −1.6% |
| V3/F (L) | 120 | 117.4 | 114.6 | −4.5% |
| KA (1/h) | 1.1 | 1.14 | 1.13 | +3.1% |
| IIV CL (CV%) | 30 | 26.6 | 27.5 | −8.3% |

OFV 4397.446 (not comparable with run 1's OFV: the datasets differ). Moving from M1 to M3 shifts CL/F up
(+1.3%) and V3/F down (−2.4%), the direction expected when low late concentrations are informative again;
both runs remain within tolerance.

## Step 5 — Out-of-scope request

Prompt: *"Add a time-varying covariate on CL"* → the extractor reports it under `unsupported_requests` →
validator: **"out of scope – expert review; unsupported request: time-varying covariate on CL"**.
The rejection is written to the audit log and listed on the run-history page; **no run is created**.

## Step 6 — Determinism

- Run 1's script is re-rendered from its stored spec: hash `f4123a0e77a2b3bd…` → **identical**; template hash unchanged.
- Run 1 is re-executed as a child `replay` run: same script hash, same prepared-data hash, OFV 4372.51868962966 in both, **maximum relative difference across estimates = 0.0** (tolerance 1e-6).

## Step 7 — Summary and run history

The console prints both comparison tables and the path of `runs/index.html`, which shows the run tree
(run 1 → run 2 sensitivity, run 1 → replay), the side-by-side comparison, the rejected request, the
determinism checks and the audit trail.

## Portability illustration (bonus)

The same demo was also executed outside the container, on a Mac with R 4.2.2 and nlmixr2 2.0.9:

| | Mac (R 4.2.2, nlmixr2 2.0.9) | Container (R 4.6.1, nlmixr2 7.0.1) |
|---|---:|---:|
| Raw data hash | `1cb7adff…` | `1cb7adff…` |
| Script hash (run 1) | `f4123a0e…` | `f4123a0e…` |
| Prepared data hash (run 1) | `643cf77a…` | `643cf77a…` |
| Outer optimiser (nlmixr2 default) | nlminb | bobyqa |
| OFV run 1 | 4372.502 | 4372.519 |
| CL/F run 1 | 4.091 | 4.089 |
| Replay on the same platform | identical (Δ = 0.0) | identical (Δ = 0.0) |

Same inputs and the same script, but different package versions: estimates differ in the third or fourth
significant digit, and the manifests show exactly why (`packages.r`). With the same image, results are
identical bit for bit. This is the argument for asking the contractor to deliver a pinned container and
manifest-based reproducibility rather than "install instructions".

## Questions this demo lets you put to a contractor

1. How is the LLM prevented from emitting code, and from silently approximating an unsupported request? (Here: schema-constrained envelope + explicit `unsupported_requests` + closed catalogue.)
2. What exactly leaves the agency's network, and where is the pseudonym mapping kept?
3. Which manifest fields guarantee that another agency reproduces a run, and how is that tested in CI?
4. How are template changes versioned and validated before use?
5. How does the tool word differences so that it never concludes on the applicant's analysis?
