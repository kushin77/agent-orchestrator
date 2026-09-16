"""Fleet Operator instruction set (issue #953, parent #878).

hermes and paperclip are onboarded provider agents (gateway/providers/hermes.py,
gateway/providers/paperclip.py) with no operator persona in the model-agnostic
instruction layer before this. control-plane/instructions/fleet-operator/
supplies one canonical source (directive flow, approval authority,
escalation, budget checks for both agents) and its rendered mirrors, reusing
this lane's own renderer/conformance checker unchanged (no fleet-operator-
specific render path), exactly like csuite/ does for the five C-suite seats.

This proves the not-hand-forked guarantee (AC1) the same way
tests/test_render_deterministic.py proves it for example/: regenerating the
canonical source over its own inputs must reproduce the committed mirrors and
distribution manifest byte-for-byte, and the four mirrors must be
semantically conformant.
"""

from __future__ import annotations

import json
import os

import aoi
from aoi.conformance import conformance_check
from aoi.model import load_canonical
from aoi.render import MIRROR_TARGETS, render_all

_INSTR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PERSONA = os.path.join(_INSTR, "fleet-operator")
_RENDERED = os.path.join(_PERSONA, "rendered")
_CANONICAL = os.path.join(_PERSONA, "canonical.yaml")


def _canonical():
    return load_canonical(_CANONICAL)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_all_four_targets_are_rendered():
    files = render_all(_canonical())
    assert set(files) == set(MIRROR_TARGETS) == {"AGENTS.md", "CLAUDE.md", ".cursorrules", "copilot-instructions.md"}


def test_committed_mirrors_are_byte_stable_regenerations():
    """The not-hand-forked guarantee: regeneration == committed bytes."""
    canonical = _canonical()
    files = render_all(canonical)
    for name in MIRROR_TARGETS:
        committed = _read(os.path.join(_RENDERED, name))
        assert files[name] == committed, f"{name} was hand-forked (regeneration differs from the committed mirror)"


def test_committed_distribution_manifest_matches_regeneration():
    committed = json.loads(_read(os.path.join(_RENDERED, "distribution-manifest.json")))
    assert committed == aoi.distribution_manifest(_canonical())


def test_conformance_across_all_mirrors():
    canonical = _canonical()
    compliant, findings = conformance_check(_RENDERED, canonical, [])
    assert compliant, findings


def test_canonical_states_directive_flow_approval_escalation_and_budget():
    """The four things issue #953 asked the persona to state, present as rules."""
    canonical = _canonical()
    layer_ids = {layer["id"] for layer in canonical["layers"]}
    assert {"directive-flow", "approval-authority", "escalation", "budget-checks"} <= layer_ids

    rule_ids = {rule["id"] for layer in canonical["layers"] for rule in layer["rules"]}
    assert "directive--hermes-head" in rule_ids
    assert "directive--paperclip-seam" in rule_ids
    assert "budget--check-before-directive" in rule_ids
    assert "escalation--cap-approach" in rule_ids
