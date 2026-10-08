"""Pseudonymisation of assessor text before it can reach a language model.

Product / procedure / study identifiers (configurable list + regexes in
config/settings.toml) are replaced by stable tokens such as [PRODUCT_1].
The token -> identifier mapping is written only to the local audit store
(runs/audit/pseudonym_map.jsonl), which is never sent anywhere or published.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from common.core import append_jsonl, audit_dir, now_iso, settings


@dataclass
class Pseudonymised:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # token -> original


def pseudonymise(text: str, cfg: dict | None = None) -> Pseudonymised:
    cfg = cfg or settings()["pseudonymisation"]
    mapping: dict[str, str] = {}
    reverse: dict[str, str] = {}
    counters: dict[str, int] = {}

    def token_for(original: str, kind: str) -> str:
        if original not in reverse:
            counters[kind] = counters.get(kind, 0) + 1
            tok = f"[{kind}_{counters[kind]}]"
            reverse[original] = tok
            mapping[tok] = original
        return reverse[original]

    out = text
    for name in cfg.get("identifiers", []):
        out = re.sub(rf"\b{re.escape(name)}\b", lambda m: token_for(m.group(0), "PRODUCT"), out, flags=re.IGNORECASE)
    for pat in cfg.get("patterns", []):
        out = re.sub(pat["regex"], lambda m, k=pat["token"]: token_for(m.group(0), k), out)
    return Pseudonymised(out, mapping)


def store_mapping(job_id: str, mapping: dict[str, str]) -> None:
    if mapping:
        append_jsonl(audit_dir() / "pseudonym_map.jsonl", {"ts": now_iso(), "job_id": job_id, "mapping": mapping})


def reidentify(text: str, mapping: dict[str, str]) -> str:
    """Local-only: restore identifiers for display to the assessor."""
    for tok, orig in mapping.items():
        text = text.replace(tok, orig)
    return text
