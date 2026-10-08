"""End-to-end demo scenario (called by ./run_demo.sh).

1. synthetic data (done by run_demo.sh)    2. run 1, primary, BLQ M1 after clarification
3. spec shown + auto-confirm, report       4. run 2, sensitivity (M3), child of run 1
5. out-of-scope request rejected           6. determinism: re-render + re-run of run 1
7. console summary + run-history page
"""
from __future__ import annotations

import json
import sys

import typer

from common.core import runs_dir
from harness.extractors import get_extractor
from orchestrator import pipeline, runs
from orchestrator.cli import make_answer, make_confirm
from reporting.report import build_history

PROMPT_RUN1 = ("Reproduce the applicant's model for XYZ-123: two compartments, first-order absorption, combined "
               "error, IIV on CL, V2 and KA, FOCE-I. Reported: CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, "
               "V3/F 120 L, KA 1.1 /h, IIV on CL 30% CV.")
ANSWER_BLQ = "M1, as declared in the report"
PROMPT_RUN2 = "Same model, but handle BLQ with M3."
PROMPT_OOS = "Add a time-varying covariate on CL"


def step(n: int, title: str) -> None:
    typer.secho(f"\n{'=' * 78}\nSTEP {n}: {title}\n{'=' * 78}", bold=True, fg="cyan")


def main(auto_confirm: bool = typer.Option(False, "--auto-confirm"),
         skip_replay: bool = typer.Option(False, "--skip-replay", help="Skip the (slow) re-run of run 1")):
    extractor = get_extractor()
    typer.echo(f"Extractor: {extractor.name} ({extractor.model}); run store: {runs_dir()}")
    confirm = make_confirm(auto_confirm)

    step(2, "Run 1 — primary analysis (reproduce the applicant's model)")
    typer.echo(f"Assessor: {PROMPT_RUN1}")
    r1 = pipeline.submit(PROMPT_RUN1, confirm=confirm, answer=make_answer([ANSWER_BLQ]), extractor=extractor)
    typer.echo(f"=> {r1.status} {r1.run_id or ''} {r1.reasons}")
    if r1.status != "completed":
        typer.secho("Run 1 did not complete; stopping the demo.", fg="red")
        raise typer.Exit(1)

    step(4, "Run 2 — refinement: BLQ handled with M3 (child of run 1)")
    typer.echo(f"Assessor: {PROMPT_RUN2}")
    r2 = pipeline.submit(PROMPT_RUN2, parent_id=r1.run_id, confirm=confirm, answer=make_answer([]), extractor=extractor)
    typer.echo(f"=> {r2.status} {r2.run_id or ''} {r2.reasons}")

    step(5, "Out-of-scope request")
    typer.echo(f"Assessor: {PROMPT_OOS}")
    r3 = pipeline.submit(PROMPT_OOS, parent_id=r1.run_id, confirm=confirm, answer=make_answer([]), extractor=extractor)
    typer.secho(f"=> {r3.status}: {'; '.join(r3.reasons)}", fg="red" if r3.status == "rejected" else "yellow")

    step(6, "Determinism check on run 1")
    rr = pipeline.rerender_check(r1.run_id)
    typer.echo(f"Re-render from stored spec: identical script hash = {rr['identical']} ({rr['rerendered_script_hash'][:16]}…)")
    rp = None
    if not skip_replay:
        rp = pipeline.replay(r1.run_id)
        typer.echo(f"Re-run {rp['replay_run_id']}: max relative difference {rp['max_relative_difference']:.2e} "
                   f"-> identical within tolerance = {rp['estimates_identical_within_tolerance']}")

    step(7, "Summary")
    history = build_history()
    summary = {"run1": r1.run_id, "run2": r2.run_id, "out_of_scope": {"status": r3.status, "reasons": r3.reasons},
               "rerender": rr, "replay": rp, "history_page": str(history), "extractor": extractor.name}
    for label, rid in (("Run 1 (primary, M1)", r1.run_id), ("Run 2 (M3)", r2.run_id)):
        if not rid:
            continue
        res = runs.load_json(runs.run_path(rid) / "results.json")
        m = runs.load_manifest(rid)
        typer.secho(f"\n{label}: {rid}  [{m['analysis_type']}]  OFV={res['engine']['ofv']:.3f}  "
                    f"comparable={res['comparable']}", bold=True)
        typer.echo(f"  {'parameter':<16}{'applicant':>10}{'independent':>13}{'Δ':>9}  flag")
        for c in res["comparison"]:
            ind = f"{c['independent']:.3g}" if c["independent"] is not None else "–"
            d = f"{100 * c['rel_diff']:+.1f}%" if c["rel_diff"] is not None else "–"
            typer.echo(f"  {c['parameter']:<16}{c['applicant']:>10.3g}{ind:>13}{d:>9}  {c['flag']}")
        summary[f"{label}"] = {"ofv": res["engine"]["ofv"], "comparison": res["comparison"],
                               "checks": res["checks"], "qc": res["qc"]["summary"]}
    (runs_dir() / "demo_summary.json").write_text(json.dumps(summary, indent=2))
    typer.secho(f"\nOut-of-scope request: {r3.status} — {'; '.join(r3.reasons)}", fg="red")
    typer.secho(f"\nRun-history page: {history}", bold=True, fg="green")

    ok = r1.status == r2.status == "completed" and r3.status == "rejected" and rr["identical"] \
        and (rp is None or rp["estimates_identical_within_tolerance"])
    if not ok:
        typer.secho("Demo finished with unexpected outcomes (see above).", fg="red")
        sys.exit(1)


if __name__ == "__main__":
    typer.run(main)
