"""Unit tests for the control-verb vocabulary (issue #553, EPIC #551).

These exercise the validator's own logic — the closed sets, the audit rule and
the cross-reference — rather than re-asserting the YAML by hand, so a registry
edit that breaks a rule fails here with a named reason.

The composite gate covers this suite: `scripts/verify.sh` runs `pytest-control`
over `control-plane/control/tests` and `control-verbs` over the registry, and
`scripts/pytest-suites.txt` declares the suite (RC-8/#559 wired it).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CLI_PATH = ROOT / "control-plane" / "control" / "cli.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("control_verbs_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()
REGISTRY = cli.load_registry()
VERBS = REGISTRY["verbs"]


# --- the registry itself ----------------------------------------------------

def test_registry_declares_the_five_lever_files():
    assert set(cli.SOURCES) == {
        "fleet/control.py",
        "fleet/channel.py",
        "governance/dispatch/cli.py",
        "governance/reconcile/cli.py",
        "governance/lifecycle/cli.py",
    }


@pytest.mark.parametrize("path", sorted(cli.SOURCES))
def test_reader_finds_exactly_the_expected_verbs(path):
    """A file that silently loses a verb must fail the gate, not pass vacuously."""
    assert cli.verbs_in(path) == cli.SOURCES[path]


def test_every_local_verb_is_declared_exactly_once():
    declared = {}
    for v in VERBS:
        if v["source"] in cli.SOURCES:
            key = (v["source"], v["local"])
            assert key not in declared, f"{key} declared twice"
            declared[key] = v["id"]
    for path, expected in cli.SOURCES.items():
        for name in expected:
            assert (path, name) in declared, f"{path}:{name} is not declared"


def test_verb_ids_are_unique_and_namespaced():
    ids = [v["id"] for v in VERBS]
    assert len(ids) == len(set(ids))
    for vid in ids:
        family, _, action = vid.partition(".")
        assert family and action, f"{vid} is not <family>.<action>"
        assert vid == vid.lower()


# --- the closed sets --------------------------------------------------------

def test_effect_class_is_always_in_the_closed_set():
    closed = set(REGISTRY["effect_classes"])
    for v in VERBS:
        assert v["effect_class"] in closed, v["id"]


def test_refusal_codes_are_always_in_the_closed_set():
    closed = {int(k) for k in REGISTRY["refusals"]}
    for v in VERBS:
        assert set(v["refusals"]) <= closed, v["id"]


def test_every_verb_carries_the_auth_scaffold():
    """401/403/503 are unconditional: every verb can be unauthorised, forbidden or unreachable."""
    for v in VERBS:
        codes = set(v["refusals"])
        missing = {401, 403, 503} - codes
        assert not missing or v.get("note"), f"{v['id']} omits {sorted(missing)}"


# --- the audit rule (ADR-0025 D3) -------------------------------------------

def test_reads_are_never_audited_and_everything_else_is():
    for v in VERBS:
        if v["effect_class"] == "read":
            assert v["audit"] is None, f"{v['id']}: a read must not claim an audit action"
        else:
            assert v["audit"], f"{v['id']}: {v['effect_class']} requires an audit action"


# --- withholding is explained, never silent ---------------------------------

def test_exposed_verbs_have_no_withholding_reason():
    for v in VERBS:
        if v["exposed"]:
            assert "why_not_exposed" not in v, v["id"]


def test_withheld_verbs_always_say_why():
    withheld = [v for v in VERBS if not v["exposed"]]
    assert withheld, "the registry should withhold at least one verb (tmux attach)"
    for v in withheld:
        assert v.get("why_not_exposed"), f"{v['id']} is withheld with no reason"


def test_tmux_attach_is_withheld():
    """RC-9 demotes tmux; a remote caller has no tty, so these must not be exposed."""
    by_id = {v["id"]: v for v in VERBS}
    for vid in ("fleet.live", "fleet.attach"):
        assert by_id[vid]["exposed"] is False
        assert "tmux" in by_id[vid]["why_not_exposed"].lower()


# --- the real tree is clean, and the validator can fail ---------------------

def test_the_real_registry_has_no_findings():
    assert cli.validate_schema(REGISTRY) == []
    assert cli.cross_reference(REGISTRY) == []


def _mutate(mutator):
    doc = copy.deepcopy(REGISTRY)
    mutator(doc)
    return cli.validate_schema(doc)


def test_an_unknown_effect_class_is_refused_by_name():
    def m(doc):
        doc["verbs"][0]["effect_class"] = "obliterate"
    findings = _mutate(m)
    assert any("effect_class" in f and "obliterate" in f for f in findings)


def test_an_unknown_refusal_code_is_refused_by_name():
    def m(doc):
        doc["verbs"][0]["refusals"] = [401, 403, 599]
    findings = _mutate(m)
    assert any("599" in f and "outside the closed set" in f for f in findings)


def test_a_read_with_an_audit_action_is_refused():
    def m(doc):
        read = next(v for v in doc["verbs"] if v["effect_class"] == "read")
        read["audit"] = "fleet.sneaky"
    findings = _mutate(m)
    assert any("a read verb must have audit: null" in f for f in findings)


def test_a_non_read_without_an_audit_action_is_refused():
    def m(doc):
        hold = next(v for v in doc["verbs"] if v["effect_class"] == "hold")
        hold["audit"] = None
    findings = _mutate(m)
    assert any("requires an audit action" in f for f in findings)


def test_a_duplicate_id_is_refused():
    def m(doc):
        doc["verbs"].append(copy.deepcopy(doc["verbs"][0]))
    findings = _mutate(m)
    assert any("duplicate verb id" in f for f in findings)


def test_a_withheld_verb_without_a_reason_is_refused():
    def m(doc):
        v = doc["verbs"][0]
        v["exposed"] = False
        v.pop("why_not_exposed", None)
    findings = _mutate(m)
    assert any("requires why_not_exposed" in f for f in findings)


def test_a_registry_verb_with_no_local_lever_is_refused():
    doc = copy.deepcopy(REGISTRY)
    doc["verbs"].append({
        "id": "fleet.ghost", "source": "fleet/control.py", "local": "ghost",
        "effect_class": "hold", "capability": "fleet:operate",
        "audit": "fleet.ghost", "idempotent": True, "exposed": True,
        "refusals": [401, 403, 503],
    })
    findings = cli.cross_reference(doc)
    assert any("ABSENT" in f and "ghost" in f for f in findings)


def test_an_omitted_local_verb_is_refused():
    doc = copy.deepcopy(REGISTRY)
    doc["verbs"] = [v for v in doc["verbs"] if v["id"] != "fleet.stop"]
    findings = cli.cross_reference(doc)
    assert any("MISSING" in f and "'stop'" in f for f in findings)


# --- contract-first: no surface change without a contract entry ------------

# The #367/#677 incident: a producer lane added verbs to fleet/channel.py
# without declaring them in verbs.yaml, and the gate did not refuse the surface
# change by name. Contract-first (#697) makes the registry the single source of
# truth: a surface-only change is refused, and the matching contract entry clears
# it — proven end-to-end against the real lever file.

_CHANNEL = ROOT / "fleet" / "channel.py"
_PROBE = 'add_parser("probe-verb", help="contract-first probe")'


def _inject_surface_verb(path: Path, statement: str):
    """Append one add_parser(...) statement to the surface, returning the undo."""
    original = path.read_text(encoding="utf-8")
    path.write_text(original.rstrip("\n") + "\n\n" + statement + "\n", encoding="utf-8")
    return original


def _probe_registry_entry() -> dict:
    return {
        "id": "channel.probe-verb",
        "source": "fleet/channel.py",
        "local": "probe-verb",
        "effect_class": "read",
        "capability": "fleet:read",
        "audit": None,
        "idempotent": True,
        "exposed": False,
        "why_not_exposed": "Contract-first probe; internal only.",
        "refusals": [401, 403, 503],
    }


def test_surface_verb_without_contract_entry_is_refused():
    """A producer that adds a verb to fleet/channel.py WITHOUT landing the
    matching verbs.yaml + schema entry is refused by name, with rc != 0."""
    original = _CHANNEL.read_text(encoding="utf-8")
    assert "probe-verb" not in original  # the surface does not yet carry the verb
    before = hashlib.sha256(_CHANNEL.read_bytes()).hexdigest()
    try:
        _inject_surface_verb(_CHANNEL, _PROBE)
        after = hashlib.sha256(_CHANNEL.read_bytes()).hexdigest()
        # Mutation proof: the producer change really landed on the surface.
        assert before != after

        findings = cli.cross_reference(REGISTRY)
        assert any(
            "MISSING" in f and "probe-verb" in f for f in findings
        ), f"the gate must name the missing contract entry; got {findings}"

        # The full gate refuses the surface-only change (rc != 0).
        assert cli.main(["validate"]) == 1
    finally:
        _CHANNEL.write_text(original, encoding="utf-8")


def test_matching_contract_entry_makes_the_surface_change_pass():
    """Landing the matching verbs.yaml entry (the contract) clears the same
    surface change — a producer is not refused once the contract moved first."""
    original = _CHANNEL.read_text(encoding="utf-8")
    try:
        _inject_surface_verb(_CHANNEL, _PROBE)
        doc = copy.deepcopy(REGISTRY)
        doc["verbs"].append(_probe_registry_entry())
        findings = cli.cross_reference(doc)
        assert findings == [], f"the matching contract entry must clear the gate; got {findings}"
    finally:
        _CHANNEL.write_text(original, encoding="utf-8")


# --- the YAML is the single source, not a copy ------------------------------

def test_the_registry_file_is_the_one_we_loaded():
    on_disk = yaml.safe_load(
        (ROOT / "control-plane" / "control" / "verbs.yaml").read_text(encoding="utf-8")
    )
    assert len(on_disk["verbs"]) == len(VERBS)
