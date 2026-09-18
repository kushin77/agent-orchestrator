"""The recorded label vocabulary, and the filing defaults it resolves (issue #1160).

`filing-check` proved that the filing path DERIVES declaring labels; it could not
see whether the repository HAS them. `gh issue create` refuses a label that does not
exist, so `filing.defaults.area: governance` — which derives `area:governance` —
made every filing that left `area` to the default fail in production, with the gate
green. These tests hold the resolution half: the shipped policy and the shipped
inventory must resolve, a default naming an unrecorded label must be REFUSED by
name (the policy file, the label and the one refresh verb), and an inventory that
cannot be read must fail CLOSED rather than resolve everything by accident.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from checker import (
    CODE_FILING_LABEL_UNRESOLVED,
    LABELS_REFRESH_VERB,
    LabelsUnavailable,
    audit_filing_labels,
    default_filing_labels,
    load_label_inventory,
    load_policy,
    record_label_inventory,
    unresolvable_labels,
)
from filing import FilingRequest, plan_filing

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_POLICY = ROOT / "governance" / "conformance" / "policy.yaml"
SHIPPED_LABELS = ROOT / "governance" / "conformance" / "labels.json"

# A label this repository will not have: the probe must be provably absent, or the
# expectation it drives would pass without demonstrating the refusal.
PROBE_LABEL = "area:conformance-label-probe"


def _inventory_file(tmp_path: Path, payload) -> Path:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# -- the loader --------------------------------------------------------------


def test_the_recorded_inventory_loads(tmp_path: Path):
    path = _inventory_file(tmp_path, {"labels": ["area:board", "type:feature"]})
    assert load_label_inventory(path) == frozenset({"area:board", "type:feature"})


def test_a_missing_inventory_is_unavailable_and_names_the_refresh_verb(tmp_path: Path):
    """ABSENCE FAILS CLOSED: no inventory cannot mean 'every label resolves'."""
    with pytest.raises(LabelsUnavailable) as excinfo:
        load_label_inventory(tmp_path / "absent.json")
    assert LABELS_REFRESH_VERB in str(excinfo.value)


def test_an_unparseable_inventory_is_unavailable(tmp_path: Path):
    path = tmp_path / "labels.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LabelsUnavailable) as excinfo:
        load_label_inventory(path)
    assert "not valid JSON" in str(excinfo.value)
    assert LABELS_REFRESH_VERB in str(excinfo.value)


def test_an_empty_inventory_is_unavailable(tmp_path: Path):
    """An empty vocabulary would resolve nothing — or, worse, everything."""
    with pytest.raises(LabelsUnavailable) as excinfo:
        load_label_inventory(_inventory_file(tmp_path, {"labels": []}))
    assert "records no `labels` list" in str(excinfo.value)


def test_a_non_mapping_inventory_is_unavailable(tmp_path: Path):
    with pytest.raises(LabelsUnavailable):
        load_label_inventory(_inventory_file(tmp_path, ["area:board"]))


# -- the resolution helper ---------------------------------------------------


def test_unresolvable_labels_keeps_order_and_dedupes():
    labels = ("class:enterprise", "area:board", "area:ghost", "type:ghost", "area:ghost")
    assert unresolvable_labels(labels, {"class:enterprise", "area:board"}) == (
        "area:ghost",
        "type:ghost",
    )


def test_default_filing_labels_is_the_seams_own_plan(policy):
    """The gate grades the seam's output, never a second derivation that could drift."""
    assert default_filing_labels(policy) == tuple(
        plan_filing(FilingRequest(title="t", body="b"), policy).labels
    )


# -- the shipped pair --------------------------------------------------------


def test_the_shipped_policy_and_inventory_resolve():
    """The artifact pair master ships: a clean policy is ACCEPTED."""
    policy = load_policy(SHIPPED_POLICY)
    inventory = load_label_inventory(SHIPPED_LABELS)
    assert audit_filing_labels(policy, inventory) == ()


def test_the_default_area_is_a_label_this_repository_records():
    """The remedy for #1160, pinned: the derived default must resolve."""
    policy = load_policy(SHIPPED_POLICY)
    inventory = load_label_inventory(SHIPPED_LABELS)
    derived = default_filing_labels(policy)
    assert "area:%s" % policy.filing_defaults["area"] in derived
    assert "area:governance" not in derived
    assert not unresolvable_labels(derived, inventory)


# -- the refusal -------------------------------------------------------------


def test_a_default_naming_an_unrecorded_label_is_refused_by_name(policy):
    inventory = load_label_inventory(SHIPPED_LABELS)
    assert PROBE_LABEL not in inventory, (
        "%s is recorded, so this probe cannot demonstrate the refusal" % PROBE_LABEL
    )
    planted = replace(
        policy,
        filing_defaults=dict(policy.filing_defaults, area=PROBE_LABEL.split(":", 1)[1]),
    )

    findings = audit_filing_labels(
        planted, inventory, policy_path=SHIPPED_POLICY, inventory_path=SHIPPED_LABELS
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == CODE_FILING_LABEL_UNRESOLVED
    assert finding.subject == str(SHIPPED_POLICY)  # names the file
    assert PROBE_LABEL in finding.message  # names the label
    assert "not found" in finding.message  # the refusal `gh` would return
    assert LABELS_REFRESH_VERB in finding.remediation  # names the one refresh verb


def test_a_resolvable_default_produces_no_finding(policy):
    """The same venue, one field changed, verdict flips: this is the control."""
    inventory = load_label_inventory(SHIPPED_LABELS)
    assert audit_filing_labels(policy, inventory) == ()


# -- the one refresh verb ----------------------------------------------------


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_refresh_records_the_live_label_set(tmp_path: Path):
    """The artifact writer both ways: a good `gh` answer records, a bad one refuses."""
    target = tmp_path / "labels.json"
    seen: list = []

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        return _Result(0, json.dumps([{"name": "area:board"}, {"name": "type:feature"}]))

    recorded, detail = record_label_inventory(target, runner=runner)

    assert recorded is True
    assert "2 label(s)" in detail
    assert seen and seen[0][:3] == ["gh", "label", "list"]
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["labels"] == ["area:board", "type:feature"]
    assert written["refresh"] == LABELS_REFRESH_VERB
    assert written["generated_at"]


def test_refresh_refuses_a_failing_gh_and_writes_nothing(tmp_path: Path):
    target = tmp_path / "labels.json"

    def runner(_argv, **_kwargs):
        return _Result(1, stderr="could not resolve host")

    recorded, detail = record_label_inventory(target, runner=runner)

    assert recorded is False
    assert "could not resolve host" in detail
    assert not target.exists()


def test_refresh_refuses_an_empty_label_set(tmp_path: Path):
    """An empty answer must not overwrite a good inventory with nothing."""
    target = tmp_path / "labels.json"

    def runner(_argv, **_kwargs):
        return _Result(0, "[]")

    recorded, detail = record_label_inventory(target, runner=runner)

    assert recorded is False
    assert "no labels" in detail
    assert not target.exists()
