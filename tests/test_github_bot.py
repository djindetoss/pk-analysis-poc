"""The GitHub conversation flow, end to end, with the mock extractor and a stub engine."""
import json

from orchestrator import github_bot as bot
from tests.test_manifest import fake_engine  # noqa: F401  (fixture)

FORM_BODY = ("### Analysis request\n\nReproduce the applicant's model for XYZ-123: two compartments, first-order "
             "absorption, combined error, IIV on CL, V2 and KA, FOCE-I. Reported: CL/F 4.2 L/h, V2/F 35 L, "
             "Q/F 8.1 L/h, V3/F 120 L, KA 1.1 /h.\n")


def test_parse_issue_form_and_commands():
    assert bot.parse_issue_body(FORM_BODY).startswith("Reproduce the applicant's model")
    assert bot.parse_command("/refine Same model, but M3.") == ("refine", "Same model, but M3.")
    assert bot.parse_command("/confirm") == ("confirm", "")
    assert bot.parse_command("M1, as declared") == (None, "M1, as declared")


def test_full_conversation(fake_engine):  # noqa: F811
    comment, action = bot.handle(7, "opened", FORM_BODY, "alice", "Reproduce XYZ-123")
    assert "Clarification needed" in comment and "LLOQ" in comment and action == "none"

    comment, action = bot.handle(7, "comment", "/confirm", "alice")
    assert "Nothing to confirm" in comment and action == "none"  # no confirmation before the spec is complete

    comment, action = bot.handle(7, "comment", "M1, as declared in the report", "alice")
    assert "Proposed analysis spec" in comment and "pk_2cmt_oral_v1" in comment and "primary" in comment

    comment, action = bot.handle(7, "comment", "Thanks, looks good", "bob")
    assert "No change to the spec" in comment and action == "none"

    comment, action = bot.handle(7, "comment", "Actually use SAEM.", "alice")
    assert "| Estimation | SAEM |" in comment  # a real correction is re-proposed
    comment, action = bot.handle(7, "comment", "Sorry, keep FOCE-I.", "alice")
    assert "| Estimation | FOCEi |" in comment

    comment, action = bot.handle(7, "comment", "/confirm", "alice")
    assert action == "execute"
    result = bot.execute(7, "alice")
    assert "Run completed" in result and "report.html" in result

    comment, action = bot.handle(7, "comment", "/refine Same model, but handle BLQ with M3.", "alice")
    assert "sensitivity analysis" in comment and "blq_method" in comment
    bot.handle(7, "comment", "/confirm", "alice")
    bot.execute(7, "alice")

    comment, action = bot.handle(7, "comment", "/refine Add a time-varying covariate on CL", "alice")
    assert "out of scope" in comment and "no run was created" in comment

    state = json.loads((bot.requests_dir() / "7.json").read_text())
    assert len(state["runs"]) == 2 and state["status"] == "rejected"
    assert bot.handle(7, "comment", "just chatting", "carol") == ("", "none")  # silent on discussion


def test_portal_lists_requests_and_never_publishes_pseudonym_map(fake_engine, tmp_path):  # noqa: F811
    from common.core import runs_dir
    from reporting.site import build_portal

    bot.handle(3, "opened", FORM_BODY, "alice", "Analysis request: reproduce XYZ-123")
    store = runs_dir().parent
    assert (store / "runs" / "audit" / "pseudonym_map.jsonl").exists()
    index = build_portal(store, tmp_path / "site")
    html = index.read_text()
    assert "#3" in html and "awaiting answer" in html and "issues/new?template=analysis-request.yml" in html
    assert (tmp_path / "site" / "runs" / "index.html").exists()
    assert not list((tmp_path / "site").rglob("pseudonym_map.jsonl"))


def test_comment_on_issue_without_state_recovers_from_issue_body(fake_engine):  # noqa: F811
    comment, action = bot.handle(9, "comment", "M1, as declared in the report", "alice", "t", issue_body=FORM_BODY)
    assert "re-read from the issue description" in comment and "Proposed analysis spec" in comment
    comment, action = bot.handle(9, "comment", "/confirm", "alice")
    assert action == "execute"
