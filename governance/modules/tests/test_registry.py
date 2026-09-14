"""The registry document: three states never two, and the derived inventory."""

from __future__ import annotations

from pathlib import Path

from conftest import manifest, refusal_subjects

from governance.modules import health, registry
from governance.modules.model import (
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REGISTERED_MANDATORY,
    SCHEMA,
    STATES,
    TARGET_PENDING,
)

REQUIRED_FIELDS = (
    "id",
    "state",
    "shipped",
    "mandatory",
    "consumer_assets",
    "owning_repo",
    "pin",
    "rev",
    "board_ref",
    "health",
)


def test_exactly_three_states_and_a_separate_refusal(built) -> None:
    assert built["schema"] == SCHEMA
    assert built["states"] == list(STATES)
    assert len(STATES) == 3
    assert built["membership_refusal"] == NOT_A_MODULE
    assert NOT_A_MODULE not in STATES
    assert NOT_A_MODULE not in [entry["state"] for entry in built["modules"]]


def test_inventory_is_derived_from_the_two_surfaces(built) -> None:
    by_state = {state: [] for state in STATES}
    for entry in built["modules"]:
        by_state[entry["state"]].append(entry["id"])
    assert sorted(by_state[REGISTERED_MANDATORY]) == ["alpha", "beta"]
    assert sorted(by_state[CATALOG_MODULE_NOT_MANDATORY]) == ["gamma"]
    assert sorted(by_state[TARGET_PENDING]) == ["zeta"]
    assert [entry["id"] for entry in built["not_modules"]] == ["delta", "epsilon", "eta"]
    assert built["summary"] == {
        REGISTERED_MANDATORY: 2,
        TARGET_PENDING: 1,
        CATALOG_MODULE_NOT_MANDATORY: 1,
        NOT_A_MODULE: 3,
    }


def test_clean_document_refuses_nothing(built) -> None:
    assert built["refusals"] == []


def test_the_catalog_wins_over_a_declared_target(built) -> None:
    """``alpha`` is declared a target AND registered mandatory — it is registered."""
    entry = next(item for item in built["modules"] if item["id"] == "alpha")
    assert entry["state"] == REGISTERED_MANDATORY
    assert entry["shipped"] is True
    assert entry["mandatory"] is True
    assert entry["pin"] == "v1.0.0"


def test_target_pending_is_never_shipped_and_names_its_blocker(built) -> None:
    entry = next(item for item in built["modules"] if item["id"] == "zeta")
    assert entry["state"] == TARGET_PENDING
    assert entry["shipped"] is False
    assert entry["mandatory"] is None  # intent is not inferred from a declaration
    assert entry["blocking"] == ["kushin77/CMR#435", "kushin77/zeta#1"]
    assert entry["board_ref"] == "kushin77/CMR#435"
    assert entry["onboarding"] == "CMR:ONBOARD-0009"
    assert entry["health"]["status"] == "absent"


def test_a_target_that_landed_unregistered_is_refused_by_name(consumer, hub, tmp_path) -> None:
    from conftest import write_targets

    targets = write_targets(
        tmp_path / "landed.json",
        {
            "schema": "ao.module-targets/v1",
            "targets": [{"id": "gamma", "repo": "kushin77/gamma", "blocking": ["kushin77/CMR#9"]}],
            "watch": [],
        },
    )
    doc = registry.build(consumer, hub, targets)
    assert refusal_subjects(doc, "MODULE-TARGET-LANDED-NOT-MANDATORY") == ["gamma"]
    gamma = next(item for item in doc["modules"] if item["id"] == "gamma")
    assert gamma["state"] == CATALOG_MODULE_NOT_MANDATORY
    assert gamma["shipped"] is True and gamma["mandatory"] is False


def test_a_target_that_landed_mandatory_is_derived_not_declared(consumer, make_hub, tmp_path) -> None:
    """The state flips when the hub lands it — target-pending is not sticky."""
    from conftest import write_targets

    modules = (
        {"dir": "alpha", "manifest": manifest("alpha", mandatory=True, assets=["alpha.json"])},
        {"dir": "zeta", "manifest": manifest("zeta", mandatory=True, assets=["zeta.json"])},
    )
    rows = [["alpha", "alpha", "alpha.json", "mandatory probe"], ["zeta", "zeta", "zeta.json", "landed"]]
    scratch = make_hub("landed", modules=modules, rows=rows, seeds=["alpha.json", "zeta.json"])
    targets = write_targets(
        tmp_path / "landed.json",
        {"schema": "ao.module-targets/v1", "targets": [{"id": "zeta", "repo": "kushin77/zeta", "blocking": ["kushin77/CMR#435"]}], "watch": []},
    )
    doc = registry.build(consumer, scratch, targets)
    assert doc["refusals"] == []
    zeta = next(item for item in doc["modules"] if item["id"] == "zeta")
    assert zeta["state"] == REGISTERED_MANDATORY
    assert doc["summary"][TARGET_PENDING] == 0


def test_every_entry_carries_the_acceptance_fields(built) -> None:
    entries = built["modules"] + built["not_modules"]
    assert entries
    for entry in entries:
        for field in REQUIRED_FIELDS:
            assert field in entry, (entry["id"], field)
        assert health.well_formed(entry["health"]), entry["id"]
        assert health.claims_ok_without_running(entry["health"], live=False) is False


def test_references_are_references(built) -> None:
    for entry in built["modules"]:
        reference = entry["reference"]
        assert reference["hub"] == built["hub"]["root"]
        assert reference["path"].startswith("catalog/modules/") or reference[
            "kind"
        ] == "declared-target"
        assert not reference["path"].startswith("/")


def test_registered_entries_resolve_their_consumer_assets(built) -> None:
    alpha = next(item for item in built["modules"] if item["id"] == "alpha")
    assert alpha["consumer_assets"] == ["alpha.json"]
    assert alpha["assets"] == [
        {
            "asset": "alpha.json",
            "seed": "templates/module/alpha.json",
            "seed_present": True,
            "present_in_repo": False,
        }
    ]


def test_findings_round_trip(built, consumer, hub, tmp_path) -> None:
    from conftest import write_targets

    targets = write_targets(
        tmp_path / "bad.json",
        {"schema": "ao.module-targets/v1", "targets": [{"id": "nothing", "repo": None, "blocking": []}]},
    )
    doc = registry.build(consumer, hub, targets)
    assert refusal_subjects(doc, "TARGET-WITHOUT-BLOCKING") == ["nothing"]
    rendered = [finding.render() for finding in registry.findings(doc)]
    assert len(rendered) == 1
    assert rendered[0].startswith("TARGET-WITHOUT-BLOCKING: nothing — ")
    assert "blocking hub issue(s)" in rendered[0]


def test_a_target_carrying_no_id_is_refused(built, consumer, hub, tmp_path) -> None:
    from conftest import write_targets

    targets = write_targets(
        tmp_path / "noid.json",
        {"schema": "ao.module-targets/v1", "targets": [{"repo": "kushin77/x", "blocking": ["kushin77/CMR#2"]}]},
    )
    doc = registry.build(consumer, hub, targets)
    assert refusal_subjects(doc, "TARGET-WITHOUT-ID") == [
        "{'repo': 'kushin77/x', 'blocking': ['kushin77/CMR#2']}"
    ]


def test_unreadable_target_set_is_cannot_assess(consumer, hub, tmp_path) -> None:
    import pytest

    from governance.modules.model import CannotAssess

    broken = tmp_path / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    with pytest.raises(CannotAssess):
        registry.build(consumer, hub, broken)


def test_entries_are_sorted_by_id(built) -> None:
    assert [entry["id"] for entry in built["modules"]] == sorted(
        entry["id"] for entry in built["modules"]
    )
    assert [entry["id"] for entry in built["not_modules"]] == sorted(
        entry["id"] for entry in built["not_modules"]
    )


def test_register_claims_never_enter_the_module_list(built) -> None:
    """``epsilon`` claims ``admission: declared``; it is still not a module."""
    assert "epsilon" not in [entry["id"] for entry in built["modules"]]
    epsilon = next(entry for entry in built["not_modules"] if entry["id"] == "epsilon")
    assert epsilon["state"] == NOT_A_MODULE
    assert epsilon["claim"]["admission"] == "declared"
    assert "does not confer membership" in epsilon["detail"]


def test_declared_inputs_are_recorded(built) -> None:
    assert built["declared"]["targets"].endswith("targets.json")
    assert built["declared"]["target_count"] == 2
    assert built["declared"]["watch_count"] == 1
    assert "submodules" in built["declared"]["register"]


def test_paths_are_recorded_relative_to_the_repo_when_possible(tmp_path, hub, targets) -> None:
    """A relative hub root stays relative, so the document is checkout-portable."""
    import shutil

    from conftest import write_repo

    repo = write_repo(tmp_path / "portable")
    shutil.copytree(hub, repo / "vendor" / "CMR")
    doc = registry.build(repo, Path("vendor/CMR"), targets)
    assert doc["hub"]["root"] == "vendor/CMR"
    assert doc["refusals"] == []
    assert str(tmp_path) not in registry.render(doc)
