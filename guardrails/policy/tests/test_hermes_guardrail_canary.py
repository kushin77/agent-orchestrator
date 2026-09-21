"""Hermes head-of-org guardrail CANARY (issue #1519, parent #1510).

`guardrails/policy/bundles/platform/hermes-head.yaml` shipped as a complete
default-deny allow-list (issue #951) whose control
(`hermes-head-guardrails`) shipped `enabled: false`, so the policy had never
been exercised against a **committed registry** — every earlier proof built its
"control ON" registry in-process, in a fixture. A guardrail that has never
fired in any environment is a declaration, not a control.

This module is the canary proof. The canary SCOPE is

    (registry: guardrails/policy/canary/controls.canary.yaml — one control, ON)
  × (bundle:   guardrails/policy/bundles/platform/hermes-head.yaml)

and this module asserts, against those committed artifacts:

* the canary registry enables **exactly** the one control, and its entry
  mirrors the shipped control field for field apart from the deliberate
  `enabled` / `on_since_rationale` / `notes` differences;
* the shipped production registry is **unchanged** — `default_controls_file()`
  is still `controls.yaml`, and it still ships the control `enabled: false`;
* a synthetic `agent.dispatch` with `channel != gateway` is **BLOCKed**, and the
  decision names the specific rule `block-directive-off-gateway` — it is *not*
  the advisory policy default or the engine's uncovered/default-deny fallback;
* the block reaches **`guardrails/policy/audit.py`'s ledger on disk**: the real
  `JsonlAuditLog` sink writes a JSON-lines record that a fresh reader (no shared
  state with the writer) reloads with `outcome="blocked"` and that rule id;
* the negative control holds — the *same* action with `channel == gateway`, and
  a model call under the budget ceiling, are **not** blocked, so the allow-list
  is not refusing everything indiscriminately;
* the flip is **load-bearing**: with the canary control's `enabled` forced back
  to False, and with the shipped registry, the named rule stops firing.

Nothing here writes policy rules — `hermes-head.yaml` is consumed unmodified.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from policy import DecisionLevel, PolicyEngine, build_bundle
from policy.audit import JsonlAuditLog
from policy.controls import ControlRegistry
from policy.startup import default_bundle_dir, default_controls_file, package_root

TARGET = "hermes-head-guardrails"
BLOCK_RULE = "block-directive-off-gateway"
ALLOW_RULE = "allow-dispatch-via-gateway"
HERMES_POLICY = "hermes-head.yaml"

CANARY_REGISTRY = package_root() / "canary" / "controls.canary.yaml"
HERMES_POLICY_PATH = Path(default_bundle_dir()) / HERMES_POLICY

#: Fields the canary entry and the shipped entry must agree on verbatim.
MIRRORED_FIELDS = ("id", "name", "description", "mode", "implemented_by", "since")

OFF_GATEWAY = {"channel": "sideband"}
VIA_GATEWAY = {"channel": "gateway"}


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def canary_document() -> dict:
    """The committed canary registry, parsed."""
    with open(CANARY_REGISTRY, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture()
def shipped_document() -> dict:
    """The committed production registry, parsed."""
    with open(default_controls_file(), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture()
def canary_controls() -> ControlRegistry:
    return ControlRegistry.load_yaml(CANARY_REGISTRY)


@pytest.fixture()
def canary_engine(canary_controls, tmp_path):
    """The canary scope's engine, sinking to a real JSON-lines ledger."""
    bundle = build_bundle([str(HERMES_POLICY_PATH)], controls=canary_controls)
    ledger = tmp_path / "hermes-canary-audit.jsonl"
    engine = PolicyEngine(bundle, controls=canary_controls, audit_log=JsonlAuditLog(ledger))
    return engine, ledger


def _ledger_records(path: Path) -> list[dict]:
    """Reload the ledger from disk exactly as an external consumer would."""
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _registry_with_flip(flip: bool) -> ControlRegistry:
    """A registry built from the shipped one, with only the target flipped.

    Used for the full-platform-bundle assertions and the load-bearing check:
    it is derived, never committed, so it cannot be mistaken for a scope.
    """
    with open(default_controls_file(), encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    for control in document["controls"]:
        if control["id"] == TARGET:
            control["enabled"] = flip
            if flip:
                control["on_since_rationale"] = "derived registry for the #1519 proof"
            else:
                control.pop("on_since_rationale", None)
    return ControlRegistry.from_mapping(document)


# --------------------------------------------------------------------------- #
# the canary scope's shape: one control, ON — and production still OFF
# --------------------------------------------------------------------------- #
def test_canary_registry_enables_exactly_the_one_control(canary_document, canary_controls):
    entries = canary_document["controls"]
    assert [entry["id"] for entry in entries] == [TARGET], (
        "the canary is a NARROW scope: it must register only the control the "
        "hermes-head policy references"
    )
    entry = entries[0]
    assert entry["enabled"] is True
    assert str(entry.get("on_since_rationale", "")).strip(), (
        "a control that ships ON must document on_since_rationale"
    )
    assert canary_controls.active_ids() == (TARGET,)
    assert canary_controls.is_active(TARGET) is True


def test_canary_entry_mirrors_the_shipped_control(canary_document, shipped_document):
    canary_entry = canary_document["controls"][0]
    shipped_entry = next(
        entry for entry in shipped_document["controls"] if entry["id"] == TARGET
    )
    for field in MIRRORED_FIELDS:
        assert canary_entry[field] == shipped_entry[field], (
            f"the canary entry drifted from the shipped control on {field!r}"
        )
    # the two deliberate differences, and only those two plus `notes`
    assert shipped_entry["enabled"] is False
    assert canary_entry["enabled"] is True
    assert "on_since_rationale" not in shipped_entry
    assert set(canary_entry) - set(shipped_entry) == {"on_since_rationale"}
    assert set(shipped_entry) - set(canary_entry) == set()


def test_production_registry_still_ships_the_control_off():
    shipped = ControlRegistry.load_yaml(default_controls_file())
    control = shipped.get(TARGET)
    assert control is not None, "the shipped registry lost the control"
    assert control.enabled is False, (
        "production must not have been promoted by this canary; promotion is a "
        "separate recorded decision (E1's ADR)"
    )
    assert shipped.is_active(TARGET) is False


def test_the_canary_is_not_the_deployed_default():
    assert Path(default_controls_file()).resolve() != CANARY_REGISTRY.resolve()
    assert default_controls_file().endswith("guardrails/policy/controls.yaml")


# --------------------------------------------------------------------------- #
# the BLOCK does fire, by name — and it reaches the ledger on disk
# --------------------------------------------------------------------------- #
def test_off_gateway_dispatch_is_blocked_by_the_named_rule(canary_engine):
    engine, _ledger = canary_engine
    result = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY
    )
    assert result.decision is DecisionLevel.BLOCK
    assert result.blocked is True
    # NOT a generic default-deny: the policy governs the action (so the
    # decision is not the engine's uncovered fallback) and the rule that fired
    # is named.
    assert result.uncovered is False
    assert result.error is None
    assert [hit.rule_id for hit in result.matched_rules] == [BLOCK_RULE]
    assert BLOCK_RULE in {hit.rule_id for hit in result.matched_rules}
    assert "gateway" in result.matched_rules[0].reason
    assert "sideband" in result.matched_rules[0].reason
    assert "hermes-head-guardrails" in result.policies_consulted


def test_the_block_records_the_named_rule_in_the_audit_ledger(canary_engine):
    engine, ledger = canary_engine
    engine.evaluate("agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY)

    assert ledger.exists(), "the --audit style sink wrote no ledger"
    records = _ledger_records(ledger)
    assert len(records) == 1
    record = records[0]
    assert record["decision"] == DecisionLevel.BLOCK.value
    assert record["outcome"] == "blocked"
    assert record["action"] == "agent.dispatch"
    assert record["subject"] == "hermes"
    assert record["rule_ids"] == [BLOCK_RULE]
    assert record["policy_ids"] == ["hermes-head-guardrails"]
    assert record["error"] is None

    # a FRESH reader with no shared state reloads the same record from disk
    reloaded = JsonlAuditLog(ledger).records()
    assert len(reloaded) == 1
    assert reloaded[0].rule_ids == (BLOCK_RULE,)
    assert reloaded[0].outcome == "blocked"


def test_the_ledger_appends_and_never_rewrites(canary_engine):
    engine, ledger = canary_engine
    engine.evaluate("agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY)
    engine.evaluate("agent.dispatch", subject="hermes", tenant="acme", context=VIA_GATEWAY)
    records = _ledger_records(ledger)
    assert [record["sequence"] for record in records] == [1, 2]
    assert [record["outcome"] for record in records] == ["blocked", "allowed"]


# --------------------------------------------------------------------------- #
# negative control: the allow-list is not refusing everything
# --------------------------------------------------------------------------- #
def test_negative_control_gateway_dispatch_passes(canary_engine):
    engine, _ledger = canary_engine
    result = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=VIA_GATEWAY
    )
    assert result.decision is DecisionLevel.LOG
    assert result.blocked is False
    assert [hit.rule_id for hit in result.matched_rules] == [ALLOW_RULE]


def test_negative_control_model_call_under_the_ceiling_passes(canary_engine):
    engine, _ledger = canary_engine
    result = engine.evaluate(
        "model.call",
        subject="hermes",
        tenant="acme",
        context={"budget": {"utilization_ratio": 0.2}},
    )
    assert result.decision is DecisionLevel.LOG
    assert result.blocked is False


def test_the_channel_is_the_only_difference_between_block_and_pass(canary_engine):
    """The two synthetic actions differ in exactly one field, so the BLOCK
    cannot be attributed to the subject, tenant, or action name."""
    engine, _ledger = canary_engine
    blocked = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY
    )
    allowed = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=VIA_GATEWAY
    )
    assert blocked.action == allowed.action == "agent.dispatch"
    assert blocked.subject == allowed.subject == "hermes"
    assert blocked.tenant == allowed.tenant == "acme"
    assert blocked.blocked is True and allowed.blocked is False


# --------------------------------------------------------------------------- #
# the flip is load-bearing (self-mutating negative control)
# --------------------------------------------------------------------------- #
def test_without_the_canary_flip_the_named_rule_never_fires(canary_document):
    """The BLOCK above is caused by the canary registry's one flip: mutate it
    back to the shipped value and the named rule must stop firing."""
    flipped_off = {"version": 1, "controls": []}
    for entry in canary_document["controls"]:
        mutated = dict(entry)
        mutated["enabled"] = False
        mutated.pop("on_since_rationale", None)
        flipped_off["controls"].append(mutated)
    registry = ControlRegistry.from_mapping(flipped_off)
    assert registry.active_ids() == ()

    bundle = build_bundle([str(HERMES_POLICY_PATH)], controls=registry)
    engine = PolicyEngine(bundle, controls=registry)
    result = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY
    )
    # the action is still refused, but by the engine's fail-closed fallback —
    # never by the named rule. That distinction is the whole point.
    assert result.uncovered is True
    assert result.matched_rules == ()
    assert BLOCK_RULE not in {hit.rule_id for hit in result.matched_rules}


def test_shipped_registry_does_not_fire_the_named_rule():
    """The differential against production: with the shipped registry the
    action is `uncovered`, so the canary registry — not some other policy — is
    what makes the named rule fire."""
    shipped = ControlRegistry.load_yaml(default_controls_file())
    bundle = build_bundle([str(HERMES_POLICY_PATH)], controls=shipped)
    engine = PolicyEngine(bundle, controls=shipped)
    result = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY
    )
    assert result.uncovered is True
    assert result.matched_rules == ()


# --------------------------------------------------------------------------- #
# the same flip holds when the WHOLE platform bundle is loaded
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("context", "expected_block"),
    [(OFF_GATEWAY, True), (VIA_GATEWAY, False)],
)
def test_full_platform_bundle_with_the_flip(context, expected_block):
    """No other active policy in `bundles/platform` interferes with the
    result: loading the full shipped bundle reproduces both halves."""
    registry = _registry_with_flip(True)
    bundle = build_bundle([default_bundle_dir()], controls=registry)
    engine = PolicyEngine(bundle, controls=registry)
    result = engine.evaluate("agent.dispatch", subject="hermes", tenant="acme", context=context)
    assert result.blocked is expected_block
    if expected_block:
        assert BLOCK_RULE in {hit.rule_id for hit in result.matched_rules}
    else:
        assert result.decision is DecisionLevel.LOG


def test_full_platform_bundle_without_the_flip_never_fires_the_rule():
    registry = _registry_with_flip(False)
    bundle = build_bundle([default_bundle_dir()], controls=registry)
    engine = PolicyEngine(bundle, controls=registry)
    result = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context=OFF_GATEWAY
    )
    assert result.uncovered is True
    assert result.matched_rules == ()
