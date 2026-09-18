"""Membership is refused, not inferred — the CMR#952 drift guard.

A name the hub catalog does not carry is refused **by name**, however loudly the
local register describes itself. That is the whole difference between a claim
and membership.
"""

from __future__ import annotations

import pytest
import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_modules_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
write_repo = _conftest.write_repo
write_targets = _conftest.write_targets

from governance.modules import registry
from governance.modules.model import (
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REGISTERED_MANDATORY,
    STATES,
    TARGET_PENDING,
)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("alpha", REGISTERED_MANDATORY),
        ("beta", REGISTERED_MANDATORY),
        ("gamma", CATALOG_MODULE_NOT_MANDATORY),
        ("zeta", TARGET_PENDING),
    ],
)
def test_a_member_resolves_to_its_state(built, name, expected) -> None:
    state, entry = registry.membership(built, name)
    assert state == expected
    assert entry["id"] == name


@pytest.mark.parametrize("name", ["delta", "epsilon", "eta"])
def test_a_non_member_is_refused_by_name(built, name) -> None:
    state, entry = registry.membership(built, name)
    assert state == NOT_A_MODULE
    assert entry["id"] == name
    assert entry["membership"] == "refused"
    assert entry["detail"]


def test_unknown_name_is_refused_by_name(built) -> None:
    state, entry = registry.membership(built, "churn-stack")
    assert state == NOT_A_MODULE
    assert entry["id"] == "churn-stack"
    assert "no declared target and no register entry names it" in entry["detail"]


def test_a_register_claim_never_confers_membership(built) -> None:
    """``epsilon`` records ``admission: declared``; the catalog carries nothing."""
    state, entry = registry.membership(built, "epsilon")
    assert state == NOT_A_MODULE
    assert entry["claim"]["admission"] == "declared"
    assert entry["owning_repo"] == "kushin77/epsilon"
    assert "does not confer membership" in entry["detail"]


def test_refusal_is_not_a_state() -> None:
    assert NOT_A_MODULE not in STATES
    assert len(STATES) == 3


def test_every_register_and_watch_name_is_accounted_for(built, consumer, hub, targets) -> None:
    names = {entry["id"] for entry in built["modules"]} | {
        entry["id"] for entry in built["not_modules"]
    }
    assert {"delta", "epsilon", "eta", "alpha", "beta", "gamma", "zeta"} <= names


def test_a_catalog_module_absent_from_the_register_is_still_a_member(built) -> None:
    """Membership comes from the catalog, never from this repo's register."""
    state, _ = registry.membership(built, "gamma")
    assert state == CATALOG_MODULE_NOT_MANDATORY


def test_register_claim_does_not_become_target_pending(consumer, hub, tmp_path) -> None:
    """A claim is not a declared target either — it is simply refused."""
    repo = write_repo(
        tmp_path / "claimant",
        register=[{"id": "theta", "repo": "kushin77/theta", "admission": "declared"}],
    )
    targets = write_targets(tmp_path / "empty-targets.json", {"schema": "ao.module-targets/v1", "targets": [], "watch": []})
    doc = registry.build(repo, hub, targets)
    assert [entry["id"] for entry in doc["modules"]] == ["alpha", "beta", "gamma"]
    theta = next(entry for entry in doc["not_modules"] if entry["id"] == "theta")
    assert theta["state"] == NOT_A_MODULE
    assert doc["summary"][TARGET_PENDING] == 0
