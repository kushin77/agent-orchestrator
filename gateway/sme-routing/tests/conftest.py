"""Test bootstrap for the SME-routing suite (issue #149).

The lane directory is hyphenated, so it cannot be an importable package name.
Like ``control-plane/instructions`` this conftest puts the module directory on
``sys.path`` and the tests import the engine by module name (``router``,
``smeroute_config``) from any cwd.

Every module in this suite is offline: no network, no clock, no fixtures
fetched from GitHub.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

import pytest
import yaml

_TESTS_DIR = Path(__file__).resolve().parent
MODULE_DIR = _TESTS_DIR.parent
POLICIES_DIR = MODULE_DIR / "policies"
SCHEMA_PATH = MODULE_DIR / "schema.yaml"

for _path in (str(MODULE_DIR), str(_TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

POLICY_FILES = ("capability-registry.yaml", "route-policy.yaml", "tier-policy.yaml")

DEFINITIONS: Mapping[str, str] = {
    "capability-registry.yaml": "capability_registry",
    "route-policy.yaml": "route_policy",
    "tier-policy.yaml": "tier_policy",
}


def read_policy(filename: str, directory: Path = POLICIES_DIR) -> Dict[str, Any]:
    with open(directory / filename, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_policy(filename: str, document: Any, directory: Path) -> Path:
    path = directory / filename
    with open(path, "w", encoding="utf-8") as handle:
        # sort_keys=False: the squads mapping's declaration order is load-bearing
        # (first declared keyword match wins), so a rewrite must not reorder it.
        yaml.safe_dump(document, handle, sort_keys=False, default_flow_style=False)
    return path


@pytest.fixture()
def module_dir() -> Path:
    return MODULE_DIR


@pytest.fixture()
def policies_dir() -> Path:
    return POLICIES_DIR


@pytest.fixture()
def schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture()
def policy_documents() -> Dict[str, Dict[str, Any]]:
    return {name: read_policy(name) for name in POLICY_FILES}


@pytest.fixture()
def router():
    from router import load_router

    return load_router()


@pytest.fixture()
def load_router_fixture() -> Callable[[Optional[Any]], Any]:
    from router import load_router

    return load_router


@pytest.fixture()
def policy_variant(tmp_path):
    """Copy the shipped policies, apply a mutation, return the copy's directory.

    The shipped files are read-only inputs: every mutation lands in ``tmp_path``,
    so the committed policies are never rewritten by a test.
    """

    def _make(mutate: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
              name: str = "policies") -> Path:
        dest = tmp_path / name
        dest.mkdir(parents=True, exist_ok=True)
        for filename in POLICY_FILES:
            document = read_policy(filename)
            if mutate is not None:
                document = mutate(filename, document)
            write_policy(filename, document, dest)
        return dest

    return _make
