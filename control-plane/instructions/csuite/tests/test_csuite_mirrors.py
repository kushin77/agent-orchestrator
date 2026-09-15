"""Acceptance suite — instruction-layer mirrors for the five C-suite personas (#643).

workbook-12 has three acceptance criteria:

1. **canonical entries for ceo/cto/coo/cfo/cmo deriving from the workbook-1
   cards and workbook-8 modules** — proved by deriving from the live artifacts
   and asserting the committed canonical sources are exactly what the
   derivation produces (a hand-written source is drift, and fails);
2. **per-tool mirrors generated deterministically, never hand-forked** —
   proved by regenerating over the same canonical source and asserting
   byte-equality with the committed mirrors (``render --check`` in-process),
   for every seat and every target;
3. **a conformance suite proving the SAME rules reach every harness** — proved
   by running the ``aoi`` conformance check over each seat's four mirrors, and
   by a NEGATIVE: a hand-forked mirror must fail.

The mutation proof (AC3's "never hand-forked" half) is the last block: a mirror
is tampered with on a scratch copy, the sha256 before/after is recorded, and the
conformance suite must produce a FAIL line.  A harness that only passes is a
formality (AO-GR-4) — so the suite is proved able to fail.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil

import pytest
import yaml

from aoi.conformance import (
    HARNESS_MAP,
    check_mirror,
    conformance_check,
    extract_ledger,
)
from aoi.model import load_canonical, rule_ids
from aoi.render import MIRROR_TARGETS, distribution_manifest, render_all
from aoi.versioning import check_drift, load_json

import derive

_CSUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CANONICAL_DIR = os.path.join(_CSUITE, "canonical")
_RENDERED_DIR = os.path.join(_CSUITE, "rendered")
_CONSUMER_DIR = os.path.join(_CSUITE, "consumer")

ROLES = derive.ROLES


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _committed_canonical(role: str) -> dict:
    return load_canonical(os.path.join(_CANONICAL_DIR, f"{role}.yaml"))


# --------------------------------------------------------------------------- #
# AC1 — canonical entries derive from the workbook-1 cards + workbook-8 modules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ROLES)
def test_canonical_source_is_committed_for_every_seat(role):
    path = os.path.join(_CANONICAL_DIR, f"{role}.yaml")
    assert os.path.isfile(path), f"{role}: canonical instruction source is missing"
    canonical = _committed_canonical(role)
    assert canonical["id"] == f"csuite-{role}"
    assert canonical["schema"] == "ao.instructions.canonical/v1"
    # Every layer is governed: the whole C-suite entry is platform-issued, so a
    # tenant override can add local rules but can never redefine these.
    assert all(layer["managed"] is True for layer in canonical["layers"])


@pytest.mark.parametrize("role", ROLES)
def test_committed_canonical_matches_the_derivation(role):
    """The canonical source is DERIVED, not authored: any hand edit is drift."""
    derived = derive.derive_canonical(role)
    committed = _committed_canonical(role)
    assert committed == derived, (
        f"{role}: the committed canonical source is not what derive.derive_canonical "
        "produces — a hand-written C-suite entry is the drift this layer prevents"
    )


@pytest.mark.parametrize("role", ROLES)
def test_derived_from_the_card(role):
    card = derive.load_card(role)
    canonical = _committed_canonical(role)
    statements = " ".join(rule["text"] for _, rule in _walk(canonical))
    assert f"tier {card['defaultModelTier']}" in statements
    assert f"${float(card['monthlyBudgetCapUsd']):.2f}" in statements
    assert card["heartbeatSchedule"] in statements
    # The reporting line: the root seat reports to the board (the principal,
    # "the board" in prose since it is not an agent); every other seat names
    # its org-chart edge explicitly.
    if card["reportsTo"] == derive.load_org_chart()["principal"]:
        assert "reports to the board" in statements
    else:
        assert f"reports to {card['reportsTo']}" in statements
    # The declared constraint set reaches the canonical source verbatim.
    for name in card["constraintSet"]:
        assert f"constraint--{name}" in rule_ids(canonical), f"{role}: constraint {name} missing"
    # The declared seat guardrails reach it too, in declaration order.
    guardrail_rules = [rule for layer in canonical["layers"]
                       if layer["id"] == "seat-expectations" for rule in layer["rules"]]
    assert [rule["text"] for rule in guardrail_rules] == [str(g) for g in card["guardrails"]]


@pytest.mark.parametrize("role", ROLES)
def test_derived_from_the_prompt_module(role):
    module = derive.load_module(role)
    enforcement = module["enforcement"]
    canonical = _committed_canonical(role)
    statements = " ".join(rule["text"] for _, rule in _walk(canonical))
    assert enforcement["policyId"] in statements
    assert enforcement["action"] in statements
    assert enforcement["attribute"] in statements
    # The mechanical layer carries exactly the workbook-6 policy declaration.
    mechanical = [layer for layer in canonical["layers"] if layer["id"] == "workbook-mechanical"]
    assert len(mechanical) == 1
    assert enforcement["policyId"] in mechanical[0]["label"]


@pytest.mark.parametrize("role", ROLES)
def test_org_chart_edge_is_encoded(role):
    chart = derive.load_org_chart()
    node = next(r for r in chart["roles"] if r["id"] == role)
    canonical = _committed_canonical(role)
    statements = " ".join(rule["text"] for _, rule in _walk(canonical))
    if node["reportsTo"] == chart["principal"]:
        assert "single root of the agent org chart" in statements
    else:
        assert f"{role} -> {node['reportsTo']}" in statements
    # The card and the org chart must agree — the derivation consumes both.
    card = derive.load_card(role)
    assert card["reportsTo"] == node["reportsTo"]
    assert card["defaultModelTier"] == node["defaultModelTier"]
    assert card["monthlyBudgetCapUsd"] == node["monthlyBudgetCapUsd"]
    assert card["heartbeatSchedule"] == node["heartbeatSchedule"]


def test_the_five_seats_are_the_csuite():
    assert set(ROLES) == {"ceo", "cto", "coo", "cfo", "cmo"}
    committed = sorted(name[:-5] for name in os.listdir(_CANONICAL_DIR) if name.endswith(".yaml"))
    assert committed == sorted(ROLES), "the committed canonical set must be exactly the five seats"


# --------------------------------------------------------------------------- #
# AC2 — mirrors are generated deterministically and are never hand-forked
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("role", ROLES)
def test_all_four_mirrors_are_committed_for_every_seat(role):
    out = os.path.join(_RENDERED_DIR, role)
    for name in list(MIRROR_TARGETS) + ["distribution-manifest.json"]:
        assert os.path.isfile(os.path.join(out, name)), f"{role}: {name} is missing"


@pytest.mark.parametrize("role", ROLES)
def test_committed_mirrors_are_byte_stable_regenerations(role):
    """The not-hand-forked guarantee: regeneration == committed bytes."""
    canonical = _committed_canonical(role)
    files = render_all(canonical, None)
    for name in MIRROR_TARGETS:
        committed = _read(os.path.join(_RENDERED_DIR, role, name))
        assert files[name] == committed, (
            f"{role}/{name} was hand-forked (regeneration differs from the committed mirror)"
        )


@pytest.mark.parametrize("role", ROLES)
def test_render_is_deterministic_across_invocations(role):
    canonical = _committed_canonical(role)
    assert render_all(canonical, None) == render_all(canonical, None)


@pytest.mark.parametrize("role", ROLES)
def test_committed_manifest_matches_regeneration(role):
    canonical = _committed_canonical(role)
    committed = json.loads(_read(os.path.join(_RENDERED_DIR, role, "distribution-manifest.json")))
    assert committed == distribution_manifest(canonical, None)


@pytest.mark.parametrize("role", ROLES)
def test_derivation_is_a_pure_function_of_the_workbook_artifacts(role):
    """Same artifacts -> same canonical source -> same mirror digests."""
    first = derive.derive_canonical(role)
    second = derive.derive_canonical(role)
    assert first == second
    assert distribution_manifest(first, None) == distribution_manifest(second, None)


@pytest.mark.parametrize("role", ROLES)
def test_committed_mirrors_have_no_trailing_whitespace(role):
    """Repo gate hygiene: committed mirrors must themselves pass docs-lint."""
    out = os.path.join(_RENDERED_DIR, role)
    for name in list(MIRROR_TARGETS) + ["distribution-manifest.json"]:
        for lineno, line in enumerate(_read(os.path.join(out, name)).splitlines(), start=1):
            assert line == line.rstrip(), f"{role}/{name}:{lineno} has trailing whitespace"


@pytest.mark.parametrize("role", ROLES)
def test_committed_canonical_has_no_trailing_whitespace(role):
    path = os.path.join(_CANONICAL_DIR, f"{role}.yaml")
    for lineno, line in enumerate(_read(path).splitlines(), start=1):
        assert line == line.rstrip(), f"{role}.yaml:{lineno} has trailing whitespace"


# --------------------------------------------------------------------------- #
# AC3 — the conformance suite: the SAME rules reach every harness
# --------------------------------------------------------------------------- #
def test_harness_map_covers_every_mirror_target():
    assert set(HARNESS_MAP) == set(MIRROR_TARGETS)


@pytest.mark.parametrize("role", ROLES)
def test_committed_mirror_set_passes_conformance(role):
    canonical = _committed_canonical(role)
    compliant, findings = conformance_check(os.path.join(_RENDERED_DIR, role), canonical, [])
    assert compliant, f"{role}: {findings}"


@pytest.mark.parametrize("role", ROLES)
def test_each_mirror_of_each_seat_matches_canonical_semantics(role):
    canonical = _committed_canonical(role)
    for name in MIRROR_TARGETS:
        path = os.path.join(_RENDERED_DIR, role, name)
        compliant, findings = check_mirror(path, canonical, [])
        assert compliant, f"{role}/{name}: {findings}"


@pytest.mark.parametrize("role", ROLES)
def test_every_mirror_carries_every_rule_statement(role):
    """A ledger may not claim a rule the mirror body does not carry."""
    canonical = _committed_canonical(role)
    for name in MIRROR_TARGETS:
        body = " ".join(_read(os.path.join(_RENDERED_DIR, role, name)).split())
        for _, rule in _walk(canonical):
            prefix = " ".join(rule["text"].split())[:40]
            assert prefix in body, f"{role}/{name} is missing rule {rule['id']} text"


@pytest.mark.parametrize("role", ROLES)
def test_every_mirror_carries_the_same_ledger_semantics(role):
    """The four harnesses must agree on the ordered rule set and precedence."""
    canonical = _committed_canonical(role)
    expected_rules = rule_ids(canonical)
    expected_precedence = [layer["id"] for layer in canonical["layers"]]
    ledgers = {}
    for name in MIRROR_TARGETS:
        ledger = extract_ledger(_read(os.path.join(_RENDERED_DIR, role, name)))
        assert ledger["tool"] == name
        assert ledger["canonical"] == {"id": canonical["id"], "version": canonical["version"]}
        assert ledger["rules"] == expected_rules
        assert ledger["precedence"] == expected_precedence
        ledgers[name] = (ledger["rules"], ledger["precedence"])
    assert len(set(map(str, ledgers.values()))) == 1, f"{role}: harnesses disagree"


@pytest.mark.parametrize("role", ROLES)
def test_derived_consumer_state_is_compliant(role):
    """The pinned consumer state matches the distribution (no drift)."""
    manifest = load_json(os.path.join(_RENDERED_DIR, role, "distribution-manifest.json"))
    consumer = load_json(os.path.join(_CONSUMER_DIR, f"{role}.json"))
    compliant, findings = check_drift(manifest, consumer)
    assert compliant, f"{role}: {findings}"


def test_seats_do_not_share_a_rule_signature():
    """Five seats, five distinct instruction sets (no copy-paste derivation)."""
    signatures = {}
    for role in ROLES:
        canonical = _committed_canonical(role)
        signatures[role] = tuple(rule_ids(canonical))
    assert len(set(signatures.values())) == len(ROLES)


# --------------------------------------------------------------------------- #
# AC3 negative — a hand-forked or wrong mirror FAILS the conformance suite
# --------------------------------------------------------------------------- #
def _scratch_render(role: str, tmp_path) -> str:
    dest = os.path.join(str(tmp_path), f"fork-{role}")
    shutil.copytree(os.path.join(_RENDERED_DIR, role), dest)
    return dest


def test_missing_mirror_fails_conformance(tmp_path):
    role = "ceo"
    dest = _scratch_render(role, tmp_path)
    os.remove(os.path.join(dest, "copilot-instructions.md"))
    compliant, findings = conformance_check(dest, _committed_canonical(role), [])
    assert not compliant
    assert any("missing" in finding for finding in findings)


def test_hand_forked_mirror_fails_conformance_with_sha256_proof(tmp_path):
    """Mutation proof: a hand-forked mirror fails, with the bytes recorded.

    ``wrong`` here is a hand edit a human would plausibly make — weakening a
    governed rule's statement in ONE harness.  The digest before/after proves
    the file really changed, and conformance must produce the FAIL line.
    """
    role = "cfo"
    dest = _scratch_render(role, tmp_path)
    path = os.path.join(dest, "CLAUDE.md")

    before = _sha256(path)
    text = _read(path)
    tampered = text.replace(
        "The cfo seat has a monthly budget cap of $50.00,",
        "The cfo seat has no monthly budget cap,",
        1,
    )
    assert tampered != text, "the mutation target statement was not found"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(tampered)
    after = _sha256(path)
    assert before != after, "the hand fork did not change the mirror"

    compliant, findings = conformance_check(dest, _committed_canonical(role), [])
    assert not compliant, "a hand-forked mirror passed conformance — the control is a formality"
    assert any("CLAUDE.md" in finding and "rule statement text missing" in finding
               for finding in findings), findings


def test_reordered_rules_in_one_mirror_fails_conformance(tmp_path):
    role = "cto"
    dest = _scratch_render(role, tmp_path)
    path = os.path.join(dest, "AGENTS.md")
    text = _read(path)
    tampered = text.replace(
        '"rules":["platform--reporting-line","platform--session-identity"',
        '"rules":["platform--session-identity","platform--reporting-line"',
        1,
    )
    assert tampered != text
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(tampered)
    compliant, findings = conformance_check(dest, _committed_canonical(role), [])
    assert not compliant
    assert any("AGENTS.md" in finding for finding in findings), findings


def test_mirror_without_ledger_fails_conformance(tmp_path):
    role = "cmo"
    dest = _scratch_render(role, tmp_path)
    path = os.path.join(dest, ".cursorrules")
    stripped = _read(path).split("<!-- ao-instructions:")[0].rstrip() + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(stripped)
    compliant, findings = conformance_check(dest, _committed_canonical(role), [])
    assert not compliant
    assert any("no instruction ledger" in finding for finding in findings), findings


def test_a_seat_rendered_from_another_seats_canonical_fails_conformance(tmp_path):
    """The wrong mirror set (another seat's) must not pass as this seat's."""
    dest = _scratch_render("coo", tmp_path)
    compliant, findings = conformance_check(dest, _committed_canonical("cfo"), [])
    assert not compliant
    assert any("canonical id" in finding for finding in findings), findings


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _walk(canonical: dict):
    for layer in canonical["layers"]:
        for rule in layer["rules"]:
            yield layer, rule
