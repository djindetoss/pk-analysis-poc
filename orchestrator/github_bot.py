"""GitHub issue bot: one analysis request = one issue; the issue thread is the conversation.

    opened issue        -> interpret the request -> clarification question | proposed spec | rejection
    plain-text reply    -> answers the pending question, or corrects the proposed spec
    /confirm            -> execute the proposed spec (the workflow runs this step without network)
    /refine <text>      -> new proposal derived from the last run (child run)
    /replay             -> determinism check on the last run
    /cancel             -> drop the pending proposal
    /help               -> list the commands

Called by .github/workflows/assessor.yml. State lives in the run store
(requests/<issue>.json), next to the runs, and is committed to the run-store branch.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from common.core import now_iso, runs_dir, settings
from harness.extractors import get_extractor
from orchestrator import pipeline, runs
from validator.catalogue import load_catalogue

HELP = """**Commands** (as the first line of a comment):
- `/confirm` – run the proposed spec
- plain text – answer a question, or correct the proposed spec before confirming
- `/refine <what to change>` – new analysis derived from the last run (child run)
- `/replay` – re-run the last run from its stored spec and compare estimates
- `/cancel` – drop the pending proposal
- `/help` – this list"""

FIELD_LABELS = [("template", "Template"), ("compartments", "Compartments"), ("absorption", "Absorption"),
                ("route", "Route"), ("error_model", "Residual error"), ("iiv", "IIV on"),
                ("estimation", "Estimation"), ("blq_method", "BLQ handling"), ("fixed_params", "Fixed parameters"),
                ("reference_values", "Applicant's reported values"), ("analysis_type", "Analysis type")]


# --------------------------------------------------------------------------- state
def requests_dir() -> Path:
    d = Path(os.environ.get("PKPOC_REQUESTS_DIR", runs_dir().parent / "requests"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state(issue: int, title: str = "") -> dict[str, Any]:
    p = requests_dir() / f"{issue}.json"
    if p.exists():
        return json.loads(p.read_text())
    return {"issue": issue, "title": title, "status": "new", "proposal": None, "runs": [],
            "last_run_id": None, "events": [], "created_at": now_iso(), "updated_at": now_iso()}


def save_state(state: dict[str, Any], event: str, by: str) -> None:
    state["events"].append({"ts": now_iso(), "event": event, "by": by})
    state["updated_at"] = now_iso()
    (requests_dir() / f"{state['issue']}.json").write_text(json.dumps(state, indent=2) + "\n")


# --------------------------------------------------------------------------- parsing
def parse_issue_body(body: str) -> str:
    """Text of the 'Analysis request' field of the issue form (or the whole body)."""
    sections = re.split(r"^###\s+(.+)$", body or "", flags=re.M)
    if len(sections) > 1:
        for heading, content in zip(sections[1::2], sections[2::2]):
            if "request" in heading.lower():
                return content.strip()
    return (body or "").strip()


def parse_command(body: str) -> tuple[str | None, str]:
    text = (body or "").strip()
    m = re.match(r"^/(\w+)\b[ \t]*(.*)$", text, flags=re.S)
    if not m:
        return None, text
    return m.group(1).lower(), m.group(2).strip()


# --------------------------------------------------------------------------- rendering
def _value(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(map(str, v)) or "–"
    if isinstance(v, dict):
        return ", ".join(f"{k} {x:g}" for k, x in v.items()) or "–"
    return "–" if v is None else str(v)


def spec_table(spec: dict[str, Any]) -> str:
    rows = [f"| {label} | {_value(spec.get(k))} |" for k, label in FIELD_LABELS if k in spec]
    return "| Field | Value |\n|---|---|\n" + "\n".join(rows)


def pages_url(path: str = "") -> str:
    return settings()["github"]["pages_url"].rstrip("/") + "/" + path


def render_proposal(p: pipeline.Proposal) -> str:
    ext = p.extractor
    via = f"{ext.get('name')} ({ext.get('model')}, prompt {ext.get('prompt_version')})"
    if p.status == "needs_clarification":
        qs = "\n".join(f"- {q}" for q in p.questions)
        known = spec_table(p.spec) if p.spec else "_nothing yet_"
        return (f"### ❓ Clarification needed\n\nThe tool does not guess missing methodology. Please answer:\n\n{qs}\n\n"
                f"Reply in plain text (one comment can answer several questions).\n\n"
                f"<details><summary>Understood so far</summary>\n\n{known}\n\n</details>\n\n_Interpreted by {via}._")
    if p.status == "rejected":
        reasons = "\n".join(f"- {r}" for r in p.reasons[1:]) or "-"
        return (f"### ⛔ {p.reasons[0]}\n\n{reasons}\n\nThe request is outside the closed template catalogue. "
                f"It has been logged and **no run was created**; it is not approximated by a nearby model.\n\n"
                f"_Interpreted by {via}._")
    if p.status == "unresolved":
        return "### ⚠️ Request could not be resolved\n\n" + "\n".join(f"- {r}" for r in p.reasons) + \
               "\n\nPlease restate the request, or open a new issue."
    # ready
    tid = p.spec["template"]
    version = load_catalogue()["templates"][tid]["version"]
    kind = p.spec["analysis_type"]
    if kind == "sensitivity":
        kind_txt = f"**sensitivity analysis** – departs from the declared methodology on: `{'`, `'.join(p.sensitivity)}`"
    else:
        kind_txt = f"**{kind} analysis**"
    parent = f" · child of run `{p.parent_id}`" if p.parent_id else ""
    return (f"### 🧾 Proposed analysis spec – please review\n\n{spec_table(p.spec)}\n\n"
            f"This is a {kind_txt}{parent}. Script generated from template `{tid}` v{version}; "
            f"the language model wrote no code and saw no data.\n\n"
            f"➡️ Reply **`/confirm`** to run it, write a correction in plain text, or `/cancel`.\n\n"
            f"_Interpreted by {via}._")


def render_run(run_id: str) -> str:
    d = runs.run_path(run_id)
    m = runs.load_manifest(run_id)
    r = runs.load_json(d / "results.json")
    checks = " · ".join(f"{c['check']}: **{c['status']}**" for c in r["checks"])
    lines = ["| Parameter | Applicant | Independent (95% CI) | Δ | Flag |", "|---|---:|---:|---:|---|"]
    for c in r["comparison"]:
        ind = "–" if c["independent"] is None else f"{c['independent']:.3g}"
        if c.get("ci_lower") is not None:
            ind += f" ({c['ci_lower']:.3g}–{c['ci_upper']:.3g})"
        d_ = "–" if c["rel_diff"] is None else f"{100 * c['rel_diff']:+.1f}%"
        lines.append(f"| {c['parameter']} | {c['applicant']:g} | {ind} | {d_} | {c['flag'] or ''} |")
    table = "\n".join(lines) if r["comparison"] else "_No applicant values in the spec._"
    flagged = r["sources_of_difference"]["flagged_parameters"]
    flag_txt = (f"Differences above tolerance: **{', '.join(flagged)}**. The report lists generic possible sources; "
                f"the tool does not determine their cause." if flagged else "No difference above tolerance.")
    ofv = (r.get("engine") or {}).get("ofv")
    status = "✅ Run completed" if m["status"] == "completed" else "❌ Run failed"
    comparable = "" if r["comparable"] else "\n\n> **Not comparable**: the estimation did not converge."
    return (f"### {status} – `{run_id}` ({m['analysis_type']})\n\n{checks}{comparable}\n\n{table}\n\n{flag_txt}\n\n"
            f"OFV {ofv:.3f} · script `{m['script_hash'][:12]}` · data `{m['input_data']['prepared_hash'][:12]}` · "
            f"R {(m['packages']['r'] or {}).get('R')}, nlmixr2 {(m['packages']['r'] or {}).get('nlmixr2')}\n\n"
            f"📄 [Full report]({pages_url(f'runs/{run_id}/report.html')}) · "
            f"🌳 [Run history]({pages_url('runs/index.html')}) (published about a minute after this comment)\n\n"
            f"Next: `/refine <what to change>` for a sensitivity analysis, or `/replay` to check reproducibility."
            if ofv is not None else f"### {status} – `{run_id}`\n\nSee the engine log in the run store.")


# --------------------------------------------------------------------------- handlers
def handle(issue: int, event: str, body: str, author: str, title: str = "",
           issue_body: str = "") -> tuple[str, str]:
    """Returns (comment_markdown, action) with action in {none, execute, replay}."""
    state = load_state(issue, title)
    extractor = get_extractor()
    prefix = ""
    if event == "comment" and state["status"] == "new" and parse_issue_body(issue_body):
        # No stored state for this issue (e.g. a step failed before saving): restart from the issue body.
        recovered, _ = handle(issue, "opened", issue_body, author, title)
        state = load_state(issue, title)
        cmd, _ = parse_command(body)
        if cmd is not None or state["status"] != "needs_clarification":
            return recovered + "\n\n_(The request was re-read from the issue description.)_", "none"
        prefix = "_(The request was re-read from the issue description.)_\n\n"

    def proposal_reply(p: pipeline.Proposal, ev: str) -> tuple[str, str]:
        state["proposal"] = p.to_dict()
        state["status"] = {"ready": "awaiting_confirmation"}.get(p.status, p.status)
        save_state(state, ev, author)
        return render_proposal(p), "none"

    if event == "opened":
        text = parse_issue_body(body)
        if not text:
            return "Please describe the analysis in the issue body.\n\n" + HELP, "none"
        return proposal_reply(pipeline.propose(text, extractor=extractor), "request_opened")

    cmd, text = parse_command(body)
    pending = pipeline.Proposal.from_dict(state["proposal"]) if state.get("proposal") else None

    if cmd == "help":
        return HELP, "none"
    if cmd == "confirm":
        if state["status"] != "awaiting_confirmation":
            return f"Nothing to confirm (status: `{state['status']}`).\n\n{HELP}", "none"
        state["status"] = "running"
        save_state(state, "confirmed", author)
        return f"✔️ Confirmed by @{author}. Running the analysis (network disabled)…", "execute"
    if cmd == "cancel":
        state["status"], state["proposal"] = "cancelled", None
        save_state(state, "cancelled", author)
        return "Proposal dropped. Nothing was run.", "none"
    if cmd == "replay":
        if not state.get("last_run_id"):
            return "No run to replay yet.", "none"
        save_state(state, "replay_requested", author)
        return f"🔁 Re-running `{state['last_run_id']}` from its stored spec…", "replay"
    if cmd == "refine":
        if not state.get("last_run_id"):
            return "No run to refine yet: confirm a proposal first.", "none"
        if not text:
            return "Say what to change, e.g. `/refine Same model, but handle BLQ with M3.`", "none"
        return proposal_reply(pipeline.propose(text, parent_id=state["last_run_id"], extractor=extractor),
                              "refine_requested")
    if cmd is not None:
        return f"Unknown command `/{cmd}`.\n\n{HELP}", "none"

    # plain text: an answer to a pending question, or a correction of a pending proposal
    if pending and state["status"] in ("needs_clarification", "awaiting_confirmation"):
        before = json.dumps(pending.spec, sort_keys=True)
        revised = pipeline.clarify(pending, text, extractor=extractor)
        if state["status"] == "awaiting_confirmation" and revised.status == "ready" \
                and json.dumps(revised.spec, sort_keys=True) == before:
            return ("No change to the spec was understood from this comment. Reply `/confirm` to run the "
                    "proposed spec, or describe the correction more explicitly."), "none"
        comment, action = proposal_reply(revised, "reply")
        return prefix + comment, action
    return "", "none"  # ordinary discussion: the bot stays silent


def execute(issue: int, author: str) -> str:
    state = load_state(issue)
    p = pipeline.Proposal.from_dict(state["proposal"])
    confirmation = {"at": now_iso(), "by": f"{author} (GitHub)", "mode": f"/confirm on issue #{issue}"}
    out = pipeline.execute_proposal(p, confirmation)
    state["runs"].append(out.run_id)
    state["last_run_id"] = out.run_id
    state["status"], state["proposal"] = out.status, None
    save_state(state, f"run_{out.status}", author)
    return render_run(out.run_id) if out.run_id else f"Run did not start: {out.reasons}"


def replay(issue: int, author: str) -> str:
    state = load_state(issue)
    rid = state["last_run_id"]
    rr = pipeline.rerender_check(rid)
    rp = pipeline.replay(rid)
    state["runs"].append(rp["replay_run_id"])
    save_state(state, "replayed", author)
    ok = rr["identical"] and rp["estimates_identical_within_tolerance"]
    largest = ", ".join(f"{k} {v:.1e}" for k, v in rp["largest_differences"].items())
    if ok:
        context = ""
    elif rp["same_cpu"]:
        context = (f"\n\nBoth runs used the same image and CPU model (`{rp['cpu_original']}`): this difference "
                   f"is unexpected and should be investigated.")
    else:
        context = (f"\n\nSame image, script and data, but different CPU models: `{rp['cpu_original']}` (original) "
                   f"vs `{rp['cpu_replay']}` (replay). Numerical libraries choose CPU-specific instructions, so "
                   f"rounding differs slightly and an optimiser can stop at a slightly different point. "
                   f"Bit-for-bit reproducibility requires the same CPU family, or pinned math libraries.")
    return (f"### {'✅' if ok else '⚠️'} Determinism check on `{rid}`\n\n"
            f"- Script re-rendered from the stored spec: {'identical' if rr['identical'] else 'DIFFERENT'} hash "
            f"(`{rr['rerendered_script_hash'][:16]}`)\n"
            f"- Re-run `{rp['replay_run_id']}`: maximum relative difference {rp['max_relative_difference']:.2e} "
            f"(tolerance {rp['tolerance']:g}); OFV {rp['ofv_original']:.6f} vs {rp['ofv_replay']:.6f}"
            f"{f'; largest: {largest}' if not ok else ''}{context}\n\n"
            f"🌳 [Run history]({pages_url('runs/index.html')})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["handle", "execute", "replay"])
    ap.add_argument("--issue", type=int, required=True)
    ap.add_argument("--event", choices=["opened", "comment"], default="comment")
    ap.add_argument("--body-file", type=Path)
    ap.add_argument("--issue-body-file", type=Path, help="issue description (to recover a request without state)")
    ap.add_argument("--author", default="unknown")
    ap.add_argument("--title", default="")
    ap.add_argument("--out", type=Path, required=True, help="markdown comment (appended)")
    ap.add_argument("--action-file", type=Path)
    a = ap.parse_args()

    action = "none"
    if a.command == "handle":
        body = a.body_file.read_text() if a.body_file else ""
        issue_body = a.issue_body_file.read_text() if a.issue_body_file else ""
        comment, action = handle(a.issue, a.event, body, a.author, a.title, issue_body)
    elif a.command == "execute":
        comment = execute(a.issue, a.author)
    else:
        comment = replay(a.issue, a.author)
    if comment:
        with open(a.out, "a") as fh:
            fh.write(comment.rstrip() + "\n\n")
    if a.action_file:
        a.action_file.write_text(action)
    print(action)


if __name__ == "__main__":
    main()
