"""Assessor command-line interface.

    python -m orchestrator.cli submit "Reproduce the applicant's model ..."
    python -m orchestrator.cli refine <run_id> "Same model, but handle BLQ with M3."
    python -m orchestrator.cli rerender <run_id>
    python -m orchestrator.cli replay <run_id>
    python -m orchestrator.cli history
"""
from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import Optional

import typer

from orchestrator import pipeline
from reporting.report import build_history

app = typer.Typer(add_completion=False, help="AI-assisted population-PK analysis (demonstrator)")


def make_confirm(auto: bool, who: str | None = None):
    def confirm(spec, analysis_type, sensitivity):
        typer.secho("\nProposed analysis spec (please review):", bold=True)
        typer.echo(json.dumps(spec, indent=2))
        typer.secho(f"Analysis type: {analysis_type}", fg="yellow" if analysis_type == "sensitivity" else "cyan")
        if sensitivity:
            typer.echo(f"Departs from the declared methodology on: {', '.join(sensitivity)}")
        if auto:
            typer.secho("--auto-confirm: spec confirmed without interaction (demo mode)", fg="magenta")
            return True
        return typer.confirm("Execute this analysis?", default=False)

    confirm.who = who or ("auto-confirm" if auto else getpass.getuser())
    confirm.mode = "auto-confirm (demo)" if auto else "interactive"
    return confirm


def make_answer(scripted: list[str]):
    queue = list(scripted)

    def answer(question: str) -> str:
        typer.secho(f"\nClarification needed: {question}", fg="yellow")
        if queue:
            reply = queue.pop(0)
            typer.echo(f"Assessor (scripted): {reply}")
            return reply
        return typer.prompt("Assessor")

    return answer


def _report(out: pipeline.Outcome) -> None:
    colour = {"completed": "green", "rejected": "red", "failed": "red"}.get(out.status, "yellow")
    typer.secho(f"\n=> {out.status.upper()}", fg=colour, bold=True)
    for r in out.reasons:
        typer.echo(f"   - {r}")
    if out.run_id:
        typer.echo(f"   run: {out.run_id}")
    typer.echo(f"   history: {build_history()}")


@app.command()
def submit(prompt: str, answer: list[str] = typer.Option([], "--answer", "-a", help="Scripted clarification answer(s)"),
           auto_confirm: bool = typer.Option(False, "--auto-confirm")):
    """Submit a new analysis request."""
    _report(pipeline.submit(prompt, confirm=make_confirm(auto_confirm), answer=make_answer(answer)))


@app.command()
def refine(parent_run_id: str, prompt: str, answer: list[str] = typer.Option([], "--answer", "-a"),
           auto_confirm: bool = typer.Option(False, "--auto-confirm")):
    """Refine an existing run (spec diff -> child run)."""
    _report(pipeline.submit(prompt, parent_id=parent_run_id, confirm=make_confirm(auto_confirm),
                            answer=make_answer(answer)))


@app.command()
def rerender(run_id: str):
    """Re-render a run's script from its stored spec and compare hashes."""
    typer.echo(json.dumps(pipeline.rerender_check(run_id), indent=2))


@app.command()
def replay(run_id: str):
    """Re-execute a run from its stored spec and compare estimates."""
    typer.echo(json.dumps(pipeline.replay(run_id), indent=2))
    build_history()


@app.command("run-spec")
def run_spec(spec_file: Path, purpose: str = typer.Option("analysis", help="analysis | experiment")):
    """Execute a stored, already confirmed spec (JSON) without the language model."""
    from validator.validate import validate_spec

    v = validate_spec(json.loads(spec_file.read_text()))
    if not v.ok:
        raise typer.BadParameter(f"spec is {v.status}: {v.reasons or v.missing}")
    out = pipeline.execute(v.spec, parent_id=None, purpose=purpose, sensitivity_fields=[], extractor=None,
                           job_ids=[], confirmation={"at": pipeline.now_iso(), "by": "system",
                                                     "mode": f"stored spec {spec_file.name}"})
    typer.echo(out.run_id)


@app.command()
def history():
    """Rebuild the run-history page."""
    typer.echo(build_history())


if __name__ == "__main__":
    app()
