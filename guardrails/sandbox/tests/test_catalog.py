"""Catalog + fail-closed resolution tests.

Prove the packaged profiles match the elevatedIQ-derived contract values, the
category map binds each closed category to the right profile, and every
unknown/undeclared resolution path fails closed to restricted.
"""

from __future__ import annotations

import os

import pytest

from sandbox.catalog import (
    default_category_map,
    default_profile_catalog,
    load_category_map,
    load_profile_catalog,
    resolve_profile,
    resolve_profile_for_category,
)
from sandbox.errors import SandboxConfigError
from sandbox.model import NETWORK_BRIDGE, NETWORK_HOST, NETWORK_NONE


def test_packaged_profiles_match_contract_values(catalog):
    restricted = catalog.profile("restricted")
    standard = catalog.profile("standard")
    privileged = catalog.profile("privileged")

    assert (restricted.network_mode, restricted.read_only_rootfs) == (
        NETWORK_NONE,
        True,
    )
    assert restricted.cap_drop == ("ALL",)
    assert (restricted.cpu_quota, restricted.memory_mb) == (50000, 512)
    assert (restricted.pids_limit, restricted.timeout_seconds) == (100, 30)

    assert (standard.network_mode, standard.memory_mb) == (NETWORK_BRIDGE, 1024)
    assert (standard.pids_limit, standard.timeout_seconds) == (200, 60)

    assert (privileged.network_mode, privileged.read_only_rootfs) == (
        NETWORK_HOST,
        False,
    )
    assert privileged.cap_drop == ("SYS_ADMIN", "SYS_PTRACE")
    assert (privileged.cpu_quota, privileged.memory_mb) == (200000, 2048)
    assert (privileged.pids_limit, privileged.timeout_seconds) == (500, 120)


def test_packaged_category_map_bindings(category_map):
    expected = {
        "file": "restricted",
        "exec": "restricted",
        "network": "standard",
        "db": "standard",
        "cloud": "standard",
        "git": "standard",
        "docker": "privileged",
        "k8s": "standard",
    }
    for category, profile in expected.items():
        assert category_map.profile_for(category) == profile
    assert category_map.default_profile == "restricted"


def test_unknown_category_fails_closed_to_restricted(category_map):
    assert category_map.profile_for("scheduler") == "restricted"
    assert category_map.profile_for("does-not-exist") == "restricted"


def test_resolve_profile_for_category_unknown_returns_restricted(catalog):
    profile = resolve_profile_for_category("scheduler", catalog=catalog)
    assert profile.name == "restricted"
    assert profile.network_mode == NETWORK_NONE


def test_resolve_profile_by_name_fails_closed_on_unknown(catalog):
    profile = resolve_profile("super-admin", catalog=catalog)
    assert profile.name == "restricted"


def test_resolve_profile_by_name_known(catalog):
    assert resolve_profile("privileged", catalog=catalog).name == "privileged"


def test_load_rejects_corrupt_profiles_document(tmp_path):
    path = os.path.join(str(tmp_path), "profiles.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        # truncated profile: missing every other required key
        handle.write(
            "defaultProfile: restricted\n"
            "profiles:\n"
            "  restricted:\n"
            "    readOnlyRootfs: true\n"
        )
    with pytest.raises(SandboxConfigError):
        load_profile_catalog(path)


def test_load_rejects_corrupt_categories_document(tmp_path):
    path = os.path.join(str(tmp_path), "categories.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("defaultProfile: restricted\ncategories:\n  file: wild\n")
    with pytest.raises(SandboxConfigError):
        load_category_map(path)


def test_load_rejects_non_mapping_yaml(tmp_path):
    path = os.path.join(str(tmp_path), "categories.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("- just\n- a\n- list\n")
    with pytest.raises(SandboxConfigError):
        load_category_map(path)


def test_defaults_are_shared_immutable_instances():
    assert default_profile_catalog() is default_profile_catalog()
    assert default_category_map() is default_category_map()
