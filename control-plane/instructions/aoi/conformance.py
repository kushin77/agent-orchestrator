"""Model-agnostic conformance suite (issue #42).

The model-agnostic proof: render ONE canonical source for every tool target
and assert structural equivalence of the semantics — the same ordered rule set
and the same precedence order are present in every mirror.  Because each
harness reads its own mirror (any model reading AGENTS.md, Claude Code reading
CLAUDE.md, Cursor reading .cursorrules, GitHub Copilot reading
copilot-instructions.md), identical rules + precedence in every mirror means a
given task yields the same behaviour regardless of harness.

Each mirror embeds a machine-readable ledger comment (rendered by
``aoi.render``).  Conformance:

1. extracts the ledger from each mirror file;
2. checks the mirror's canonical id/version, ordered rule ids and precedence
   match the canonical source (+ tenant override) they were rendered from;
3. checks every rule's statement text is actually present in the mirror body
   (so a ledger cannot claim a rule the file does not carry);
4. checks the four mirrors agree with each other (structural equivalence).

Any mismatch is a conformance failure (negative-testable).
"""

from __future__ import annotations

import json
import os

from .model import canonical_version, rule_ids
from .render import LEDGER_MARKER, LEDGER_SCHEMA, MIRROR_TARGETS
from .versioning import DistributionError

# Which harness consumes which mirror (the runtime -> file map, stated once).
HARNESS_MAP = {
    "AGENTS.md": "generic / DeepSeek harness (model-agnostic)",
    "CLAUDE.md": "Claude Code",
    ".cursorrules": "Cursor",
    "copilot-instructions.md": "GitHub Copilot",
}


class ConformanceError(ValueError):
    """Raised when a mirror's semantics diverge from the canonical source."""


def _norm(text: str) -> str:
    return " ".join(text.split())


def extract_ledger(text: str) -> dict:
    """Extract + parse the embedded instruction ledger of one mirror body."""
    start = text.find(LEDGER_MARKER)
    if start < 0:
        raise ConformanceError("mirror carries no instruction ledger (missing ao-instructions marker)")
    json_start = start + len(LEDGER_MARKER)
    end = text.find(" -->", json_start)
    if end < 0:
        raise ConformanceError("mirror ledger is not terminated")
    try:
        ledger = json.loads(text[json_start:end])
    except ValueError as exc:
        raise ConformanceError(f"mirror ledger is not valid JSON: {exc}") from exc
    if not isinstance(ledger, dict):
        raise ConformanceError("mirror ledger must be a JSON object")
    if ledger.get("schema") != LEDGER_SCHEMA:
        raise ConformanceError(f"mirror ledger schema is not {LEDGER_SCHEMA}")
    return ledger


def _expected(canonical: dict, extra_rules: list[dict]) -> tuple[str, str, list[str], list[str]]:
    expected_rules = list(rule_ids(canonical))
    expected_precedence = [layer["id"] for layer in canonical["layers"]]
    for rule in extra_rules:
        expected_rules.append(rule["id"])
    if extra_rules:
        expected_precedence.append("local")
    return canonical["id"], canonical_version(canonical), expected_rules, expected_precedence


def check_mirror(path: str, canonical: dict, extra_rules: list[dict]) -> tuple[bool, list[str]]:
    """Conformance of ONE mirror file against the canonical semantics."""
    tool = os.path.basename(path)
    findings: list[str] = []
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        return False, [f"{tool}: cannot read mirror: {exc}"]
    try:
        ledger = extract_ledger(text)
    except ConformanceError as exc:
        return False, [f"{tool}: {exc}"]

    exp_id, exp_version, exp_rules, exp_precedence = _expected(canonical, extra_rules)
    if ledger.get("tool") != tool:
        findings.append(f"{tool}: ledger tool {ledger.get('tool')!r} != filename")
    can = ledger.get("canonical") or {}
    if can.get("id") != exp_id:
        findings.append(f"{tool}: ledger canonical id {can.get('id')!r} != expected {exp_id!r}")
    if can.get("version") != exp_version:
        findings.append(f"{tool}: ledger canonical version {can.get('version')!r} != expected {exp_version!r}")
    if ledger.get("rules") != exp_rules:
        findings.append(f"{tool}: ordered rule set differs from canonical semantics")
    if ledger.get("precedence") != exp_precedence:
        findings.append(f"{tool}: precedence order differs from canonical semantics")

    # Every rule statement must actually be present in the mirror body.
    body = _norm(text)
    present_text = {rule["id"] for _, rule in _walk_rules(canonical, extra_rules)
                    if _norm(rule["text"])[:40] and _norm(rule["text"])[:40] in body}
    expected_ids = set(exp_rules)
    missing = sorted(expected_ids - present_text)
    if missing:
        findings.append(f"{tool}: rule statement text missing for: {', '.join(missing)}")
    return (not findings, findings)


def _walk_rules(canonical: dict, extra_rules: list[dict]):
    for layer in canonical["layers"]:
        for rule in layer["rules"]:
            yield layer, rule
    for rule in extra_rules:
        yield {"id": "local"}, rule


def conformance_check(rendered_dir: str, canonical: dict,
                      extra_rules: list[dict] | None = None) -> tuple[bool, list[str]]:
    """Structural-equivalence conformance over every mirror in ``rendered_dir``.

    Returns ``(compliant, findings)``; any missing/differing mirror fails.
    """
    extra_rules = list(extra_rules or [])
    findings: list[str] = []
    for tool in MIRROR_TARGETS:
        path = os.path.join(rendered_dir, tool)
        if not os.path.isfile(path):
            findings.append(f"{tool}: mirror file missing from the rendered set")
            continue
        _ok, tool_findings = check_mirror(path, canonical, extra_rules)
        findings.extend(tool_findings)
    return (not findings, findings)
