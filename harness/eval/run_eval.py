"""LLM evaluation bench: field-level accuracy of the active extractor.

    python -m harness.eval.run_eval [--extractor mock|anthropic]

Each case lists the fields it checks; `null` means the field must NOT be filled
(no guessing), `out_of_scope` checks that the validator rejects the request.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.extractors import get_extractor
from harness.harness import envelope_to_spec
from harness.pseudonymise import pseudonymise
from validator.validate import apply_diff, validate_spec

CASES = Path(__file__).resolve().parent / "cases.jsonl"


def _norm(v):
    if isinstance(v, list):
        return sorted(v)
    if isinstance(v, dict):
        return {k: float(x) for k, x in v.items()}
    return v


def evaluate(extractor=None, verbose: bool = True) -> dict:
    extractor = extractor or get_extractor()
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    total = correct = 0
    per_case = []
    for c in cases:
        current = c.get("current_spec")
        env = extractor.extract_spec(pseudonymise(c["prompt"]).text, current)
        got = envelope_to_spec(env)
        full = apply_diff(current, got) if current else got
        oos = validate_spec(full).status == "out_of_scope"
        results = {}
        for field, exp in c["expected"].items():
            actual = oos if field == "out_of_scope" else got.get(field)
            ok = _norm(actual) == _norm(exp)
            results[field] = {"expected": exp, "actual": actual, "ok": ok}
            total += 1
            correct += ok
        per_case.append({"id": c["id"], "fields": results, "all_ok": all(r["ok"] for r in results.values())})
        if verbose:
            bad = [f for f, r in results.items() if not r["ok"]]
            print(f"{'PASS' if not bad else 'FAIL'}  {c['id']:<22} {len(results) - len(bad)}/{len(results)} fields"
                  + (f"  wrong: {', '.join(bad)}" if bad else ""))
    summary = {"extractor": extractor.name, "model": extractor.model, "cases": len(cases),
               "cases_fully_correct": sum(p["all_ok"] for p in per_case),
               "field_accuracy": correct / total if total else 0.0, "fields_checked": total, "per_case": per_case}
    if verbose:
        print(f"\n{extractor.name} ({extractor.model}): field-level accuracy {correct}/{total} = "
              f"{100 * summary['field_accuracy']:.1f}%, {summary['cases_fully_correct']}/{len(cases)} cases fully correct")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", choices=["mock", "anthropic"], default=None)
    ap.add_argument("--json", type=Path, help="write the full results to this file")
    a = ap.parse_args()
    s = evaluate(get_extractor(a.extractor))
    if a.json:
        a.json.write_text(json.dumps(s, indent=2))
