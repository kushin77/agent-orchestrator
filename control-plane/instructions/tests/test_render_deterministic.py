"""Acceptance criterion 1 — canonical source + generated, never hand-forked.

The four per-tool mirrors must be deterministic, byte-stable regenerations of
the canonical source (+ tenant override).  The committed worked example under
``example/rendered/`` is the proof artifact: regenerating over the same inputs
must reproduce those files byte-for-byte, so a hand-forked mirror is a test
failure.
"""

from __future__ import annotations

import json
import os

import pytest

import aoi
from aoi.conformance import extract_ledger
from aoi.model import load_canonical
from aoi.render import MIRROR_TARGETS, render_all

_INSTR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE = os.path.join(_INSTR, "example")
_RENDERED = os.path.join(_EXAMPLE, "rendered")
_CANONICAL = os.path.join(_EXAMPLE, "canonical.yaml")
_OVERRIDE = os.path.join(_EXAMPLE, "tenant-override.json")


@pytest.fixture(scope="module")
def canonical():
    return load_canonical(_CANONICAL)


@pytest.fixture(scope="module")
def override():
    with open(_OVERRIDE, encoding="utf-8") as handle:
        return json.load(handle)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_all_four_targets_are_rendered(canonical, override):
    files = render_all(canonical, override)
    assert set(files) == set(MIRROR_TARGETS) == {"AGENTS.md", "CLAUDE.md", ".cursorrules", "copilot-instructions.md"}


def test_committed_mirrors_are_byte_stable_regenerations(canonical, override):
    """The not-hand-forked guarantee: regeneration == committed bytes."""
    files = render_all(canonical, override)
    for name in MIRROR_TARGETS:
        committed = _read(os.path.join(_RENDERED, name))
        assert files[name] == committed, f"{name} was hand-forked (regeneration differs from the committed mirror)"


def test_committed_distribution_manifest_matches_regeneration(canonical, override):
    committed = json.loads(_read(os.path.join(_RENDERED, "distribution-manifest.json")))
    assert committed == aoi.distribution_manifest(canonical, override)


def test_render_is_deterministic(canonical, override):
    first = render_all(canonical, override)
    second = render_all(canonical, override)
    assert first == second
    assert set(first) == set(second)


def test_committed_mirrors_have_no_trailing_whitespace(canonical, override):
    """Repo gate hygiene: the committed example must itself pass docs-lint."""
    for name in list(MIRROR_TARGETS) + ["distribution-manifest.json"]:
        for lineno, line in enumerate(_read(os.path.join(_RENDERED, name)).splitlines(), start=1):
            assert line == line.rstrip(), f"{name}:{lineno} has trailing whitespace"


def test_every_mirror_carries_a_matching_ledger(canonical, override):
    files = render_all(canonical, override)
    expected_rules = aoi.model.rule_ids(canonical) + ["acme-feature-branches", "acme-reviewer-required"]
    expected_precedence = ["platform", "agent-pack", "local"]
    for name, content in files.items():
        ledger = extract_ledger(content)
        assert ledger["tool"] == name
        assert ledger["canonical"]["id"] == canonical["id"]
        assert ledger["canonical"]["version"] == canonical["version"]
        assert ledger["rules"] == expected_rules
        assert ledger["precedence"] == expected_precedence


def test_override_extra_rules_reach_every_mirror(canonical, override):
    files = render_all(canonical, override)
    for name, content in files.items():
        assert "### local — Local tenant layer (tenant-owned)" in content
        assert "- **acme-feature-branches** —" in content
        assert "- **acme-reviewer-required** —" in content
