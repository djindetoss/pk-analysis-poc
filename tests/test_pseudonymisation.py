import json

from harness.extractors import MockExtractor
from harness.harness import interpret
from harness.pseudonymise import pseudonymise, reidentify

TEXT = ("Reproduce the model of XYZ-123 (Fictivumab, procedure EMEA/H/C/004567, study ABC-1234-01). "
        "Compare XYZ-123 with XYZ-456.")
IDENTIFIERS = ["XYZ-123", "XYZ-456", "Fictivumab", "EMEA/H/C/004567", "ABC-1234-01"]


def test_identifiers_replaced_with_stable_tokens():
    p = pseudonymise(TEXT)
    for ident in IDENTIFIERS:
        assert ident not in p.text
    tok = next(t for t, o in p.mapping.items() if o == "XYZ-123")
    assert p.text.count(tok) == 2  # same identifier -> same token
    assert "[PROCEDURE_1]" in p.text and "[STUDY_1]" in p.text
    assert reidentify(p.text, p.mapping) == TEXT


class SpyExtractor(MockExtractor):
    def __init__(self):
        self.seen = []

    def extract_spec(self, prompt_text, current_spec=None):
        self.seen.append(prompt_text)
        return super().extract_spec(prompt_text, current_spec)


def test_no_identifier_leaves_towards_the_llm_or_its_log(isolated_run_store):
    spy = SpyExtractor()
    interpret(TEXT, extractor=spy)
    for ident in IDENTIFIERS:
        assert all(ident not in s for s in spy.seen)
    llm_log = (isolated_run_store / "audit" / "llm_calls.jsonl").read_text()
    for ident in IDENTIFIERS:
        assert ident not in llm_log
    # the mapping is kept, but only in the local pseudonym map
    mapping = json.loads((isolated_run_store / "audit" / "pseudonym_map.jsonl").read_text().splitlines()[0])
    assert set(mapping["mapping"].values()) == set(IDENTIFIERS)
