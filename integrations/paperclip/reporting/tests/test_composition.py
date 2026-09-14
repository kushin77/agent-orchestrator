"""The composition: three honest states, every acceptance field, pending unshipped."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from governance.modules import registry as module_registry
from governance.modules.model import (
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REGISTERED_MANDATORY,
    STATES,
    TARGET_PENDING,
)
from integrations.paperclip.reporting import composer

HUB = "vendor/CMR"


def _entries(document, state: str) -> list:
    return [entry for entry in document["modules"] if entry["state"] == state]


def test_the_brief_names_the_authoritys_three_states_and_the_refusal(repo_root: Path, composition):
    for state in STATES:
        assert "`{}`".format(state) in composition.text
    assert "`{}`".format(NOT_A_MODULE) in composition.text
    assert composition.ids


def test_the_authoritys_vocabulary_is_reused_not_restated(composition):
    from integrations.paperclip.reporting import model

    assert model.STATES == STATES
    assert model.REGISTERED_MANDATORY == REGISTERED_MANDATORY
    assert model.TARGET_PENDING == TARGET_PENDING
    assert model.CATALOG_MODULE_NOT_MANDATORY == CATALOG_MODULE_NOT_MANDATORY
    assert model.NOT_A_MODULE == NOT_A_MODULE
    assert NOT_A_MODULE not in STATES


def test_every_module_is_briefed_with_the_acceptance_fields(repo_root: Path, composition):
    for row in (
        "| owning repo |",
        "| mandatory status |",
        "| pin |",
        "| rev |",
        "| consumer assets |",
        "| health |",
        "| board ref |",
        "| drift |",
    ):
        assert row in composition.text, row
    document = module_registry.build(repo_root, repo_root / HUB)
    for entry in document["modules"]:
        assert "#### `{}` — {}".format(entry["id"], entry["state"]) in composition.text


def test_every_mandatory_module_names_the_seed_each_asset_comes_from(repo_root: Path, composition):
    document = module_registry.build(repo_root, repo_root / HUB)
    mandatory = _entries(document, REGISTERED_MANDATORY)
    assert mandatory, "the registry carries no mandatory module to brief"
    for entry in mandatory:
        assert entry["pin"], entry["id"]
        assert entry["rev"], entry["id"]
        assert entry["consumer_assets"], entry["id"]
        for asset in entry["assets"]:
            assert asset["seed_present"], (entry["id"], asset["asset"])
            assert asset["seed"] in composition.text


def test_pending_is_never_rendered_as_shipped(repo_root: Path, composition):
    document = module_registry.build(repo_root, repo_root / HUB)
    pending = _entries(document, TARGET_PENDING)
    assert pending, "the registry carries no pending target to brief"
    for entry in pending:
        assert entry["shipped"] is False
        assert entry["blocking"], "a pending module must name its blocking hub issue"
        assert "#### `{}` — {}".format(entry["id"], TARGET_PENDING) in composition.text
        for blocking in entry["blocking"]:
            assert blocking in composition.text
    for line in composition.text.splitlines():
        if line.startswith("| mandatory status |") and TARGET_PENDING in line:
            assert "not shipped" in line
            assert "shipped: false" in line
    assert "shipped: true" not in composition.text


def test_a_pending_entry_that_reports_itself_shipped_is_refused_by_name(
    repo_root: Path, registry_document
):
    document = copy.deepcopy(registry_document)
    for entry in document["modules"]:
        if entry["state"] == TARGET_PENDING:
            entry["shipped"] = True
            break
    else:  # pragma: no cover - the registry always carries a target
        pytest.fail("no target-pending entry to doctor")
    findings = composer.compose(document, repo_root, HUB).findings
    codes = {finding.code for finding in findings}
    assert "BRIEF-PENDING-RENDERED-SHIPPED" in codes


def test_a_pending_entry_without_a_blocker_is_refused_by_name(repo_root: Path, registry_document):
    document = copy.deepcopy(registry_document)
    for entry in document["modules"]:
        if entry["state"] == TARGET_PENDING:
            entry["blocking"] = []
            break
    findings = composer.compose(document, repo_root, HUB).findings
    assert "BRIEF-PENDING-NO-BLOCKER" in {finding.code for finding in findings}


def test_a_mandatory_module_with_no_pin_is_refused_by_name(repo_root: Path, registry_document):
    document = copy.deepcopy(registry_document)
    entry = _entries(document, REGISTERED_MANDATORY)[0]
    entry["pin"] = None
    findings = composer.compose(document, repo_root, HUB).findings
    refused = {finding.subject for finding in findings if finding.code == "BRIEF-MODULE-NO-PIN"}
    assert refused == {entry["id"]}


def test_a_consumer_asset_with_no_seed_is_refused_by_name(repo_root: Path, registry_document):
    document = copy.deepcopy(registry_document)
    entry = _entries(document, REGISTERED_MANDATORY)[0]
    entry["assets"][0]["seed"] = None
    entry["assets"][0]["seed_present"] = False
    findings = composer.compose(document, repo_root, HUB).findings
    refused = {
        finding.subject
        for finding in findings
        if finding.code == "BRIEF-ASSET-NO-SEED"
    }
    assert refused == {entry["id"]}


def test_a_fourth_state_is_cannot_assess_not_a_silent_guess(repo_root: Path, registry_document):
    from integrations.paperclip.reporting.model import CannotAssess

    document = copy.deepcopy(registry_document)
    document["modules"][0]["state"] = "probably-fine"
    with pytest.raises(CannotAssess):
        composer.compose(document, repo_root, HUB)


def test_a_registry_whose_counts_disagree_with_its_entries_is_refused(
    repo_root: Path, registry_document
):
    document = copy.deepcopy(registry_document)
    document["summary"][REGISTERED_MANDATORY] = 99
    findings = composer.compose(document, repo_root, HUB).findings
    assert "BRIEF-SUMMARY-DRIFT" in {finding.code for finding in findings}


def test_a_registry_with_a_foreign_state_vocabulary_is_refused(
    repo_root: Path, registry_document
):
    document = copy.deepcopy(registry_document)
    document["states"] = ["mandatory", "pending"]
    findings = composer.compose(document, repo_root, HUB).findings
    assert "BRIEF-STATE-VOCABULARY-DRIFT" in {finding.code for finding in findings}


def test_refused_names_are_not_a_fourth_state(repo_root: Path, composition):
    document = module_registry.build(repo_root, repo_root / HUB)
    assert document["not_modules"], "the registry carries no refused name to report"
    for entry in document["not_modules"]:
        assert entry["state"] == NOT_A_MODULE
        assert entry["membership"] == "refused"


def test_distribution_stays_with_the_existing_channel(composition):
    for marker in (
        "controller/standards-sync.sh",
        "controller/standards-manifest.txt",
        "templates/module/",
    ):
        assert marker in composition.text
    assert "adds no second push mechanism" in composition.text
    assert "vendors nothing" in composition.text
