"""Acceptance criterion 4 — model-agnostic conformance suite.

Render ONE canonical source for every tool target and assert structural
equivalence of the semantics: the same ordered rule set and the same
precedence order are present in every mirror (AGENTS.md / CLAUDE.md /
.cursorrules / copilot-instructions.md).  Because each harness reads its own
mirror, identical semantics across mirrors means the same task yields the same
behaviour under Claude, DeepSeek/Copilot and Cursor harnesses.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from aoi.conformance import (
    HARNESS_MAP,
    ConformanceError,
    check_mirror,
    conformance_check,
    extract_ledger,
)
from aoi.model import load_canonical
from aoi.override import apply_local_rules
from aoi.render import MIRROR_TARGETS

_INSTR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE = os.path.join(_INSTR, "example")
_RENDERED = os.path.join(_EXAMPLE, "rendered")
_CANONICAL = os.path.join(_EXAMPLE, "canonical.yaml")
_OVERRIDE = os.path.join(_EXAMPLE, "tenant-override.json")


@pytest.fixture(scope="module")
def canonical():
    return load_canonical(_CANONICAL)


@pytest.fixture(scope="module")
def extra_rules(canonical):
    with open(_OVERRIDE, encoding="utf-8") as handle:
        return apply_local_rules(json.load(handle), canonical)


def _render_copy(dest):
    shutil.copytree(_RENDERED, dest)


# --- acceptance -------------------------------------------------------------

def test_harness_map_covers_every_mirror_target():
    assert set(HARNESS_MAP) == set(MIRROR_TARGETS)


def test_committed_rendered_set_passes_conformance(canonical, extra_rules):
    compliant, findings = conformance_check(_RENDERED, canonical, extra_rules)
    assert compliant, findings


def test_each_mirror_matches_canonical_semantics(canonical, extra_rules):
    for name in MIRROR_TARGETS:
        compliant, findings = check_mirror(os.path.join(_RENDERED, name), canonical, extra_rules)
        assert compliant, findings


def test_every_mirror_carries_the_rule_statements(canonical, extra_rules):
    for name in MIRROR_TARGETS:
        with open(os.path.join(_RENDERED, name), encoding="utf-8") as handle:
            body = handle.read()
        for layer in canonical["layers"]:
            for rule in layer["rules"]:
                prefix = " ".join(rule["text"].split())[:40]
                assert prefix in " ".join(body.split()), f"{name} is missing rule {rule['id']} text"
        for rule in extra_rules:
            prefix = " ".join(rule["text"].split())[:40]
            assert prefix in " ".join(body.split()), f"{name} is missing tenant rule {rule['id']} text"


# --- negatives --------------------------------------------------------------

def test_missing_mirror_fails_conformance(canonical, extra_rules, tmp_path):
    dest = os.path.join(tmp_path, "broken")
    _render_copy(dest)
    os.remove(os.path.join(dest, "copilot-instructions.md"))
    compliant, findings = conformance_check(dest, canonical, extra_rules)
    assert not compliant
    assert any("missing" in finding for finding in findings)


def test_reordered_rule_in_a_mirror_fails_conformance(canonical, extra_rules, tmp_path):
    dest = os.path.join(tmp_path, "broken")
    _render_copy(dest)
    path = os.path.join(dest, "AGENTS.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    # Swap the order of the first two ledger rule ids -> precedence/rule drift.
    tampered = text.replace(
        '"rules":["issue-first","verify-before-done"',
        '"rules":["verify-before-done","issue-first"',
        1,
    )
    assert tampered != text
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(tampered)
    compliant, findings = conformance_check(dest, canonical, extra_rules)
    assert not compliant
    assert any("AGENTS.md" in finding for finding in findings)


def test_hand_edited_rule_text_fails_conformance(canonical, extra_rules, tmp_path):
    dest = os.path.join(tmp_path, "broken")
    _render_copy(dest)
    path = os.path.join(dest, "CLAUDE.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    # A hand edit that silently rewrites a governed rule's statement.
    tampered = text.replace(
        "Work is tracked in an issue before it is done;",
        "This repo has no rules; anything goes;",
        1,
    )
    assert tampered != text
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(tampered)
    compliant, findings = conformance_check(dest, canonical, extra_rules)
    assert not compliant
    assert any("rule statement text missing" in finding for finding in findings)


def test_mirror_without_ledger_fails(canonical, extra_rules, tmp_path):
    dest = os.path.join(tmp_path, "broken")
    _render_copy(dest)
    path = os.path.join(dest, ".cursorrules")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    stripped = text.split("<!-- ao-instructions:")[0].rstrip() + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(stripped)
    with pytest.raises(ConformanceError):
        extract_ledger(stripped)
    compliant, findings = conformance_check(dest, canonical, extra_rules)
    assert not compliant
