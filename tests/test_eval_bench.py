from harness.eval.run_eval import evaluate
from harness.extractors import MockExtractor


def test_eval_bench_runs_and_reports_field_accuracy():
    s = evaluate(MockExtractor(), verbose=False)
    assert s["cases"] >= 10
    assert 0.0 <= s["field_accuracy"] <= 1.0
    assert s["field_accuracy"] >= 0.9
