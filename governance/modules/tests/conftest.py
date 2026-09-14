"""Pytest bootstrap + hermetic fixtures for ``governance/modules`` (issue #445).

``governance/`` is a PEP-420 namespace package, so the repository root goes on
``sys.path`` and the modules are imported qualified (``governance.modules.*``).

Every test that mutates something mutates a **scratch hub built in a tmp dir**
— never the pinned ``vendor/CMR`` submodule, which is read-only and is the
authority the registry reports on. The synthetic hub deliberately mirrors the
real one (two mandatory modules with seeds, one catalog module that is not
mandatory) so a fixture mutation exercises the same code path as a hub
mutation would.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: The real hub submodule; absent on a clean clone (the gate answers CANNOT-ASSESS).
REAL_HUB = REPO_ROOT / "vendor" / "CMR"
#: The package's own declared target set.
PACKAGE_TARGETS = Path(__file__).resolve().parents[1] / "targets.json"


def manifest(module_id: str, **kw: Any) -> Dict[str, Any]:
    """A ``cmr.module/v1`` manifest with only the fields a registry entry reads."""
    data: Dict[str, Any] = {"schema": "cmr.module/v1", "id": module_id, "name": kw.get("name", module_id)}
    repo = kw.get("repo", "kushin77/{}".format(module_id))
    if repo:
        data["source"] = {"repo": repo}
    if kw.get("pin"):
        data["versions"] = {"latest": kw["pin"]}
    distribution: Dict[str, Any] = {}
    if kw.get("package"):
        distribution["package"] = kw["package"]
    if kw.get("language"):
        distribution["language"] = kw["language"]
    if distribution:
        data["distribution"] = distribution
    if kw.get("direction_issue"):
        data["governance"] = {"direction_issue": kw["direction_issue"]}
    if kw.get("mandatory") is not None:
        data["mandatory"] = kw["mandatory"]
    if kw.get("assets") is not None:
        data["mandatory_consumer_assets"] = kw["assets"]
    return data


#: The synthetic catalog: two mandatory modules (with seeds) and one that is not.
BASE_MODULES: List[Dict[str, Any]] = [
    {
        "dir": "alpha",
        "manifest": manifest(
            "alpha",
            mandatory=True,
            assets=["alpha.json"],
            pin="v1.0.0",
            direction_issue="CMR:ONBOARD-0001 (#1)",
        ),
    },
    {"dir": "beta", "manifest": manifest("beta", mandatory=True, assets=["beta.yaml"], pin="v2.0.0")},
    {
        "dir": "gamma",
        "manifest": manifest("gamma", pin="v0.9.0", package="gammapkg", language="python"),
    },
]
BASE_ROWS: List[List[str]] = [
    ["alpha", "alpha", "alpha.json", "mandatory probe"],
    ["beta", "beta", "beta.yaml", "mandatory probe"],
]
BASE_SEEDS: List[str] = ["alpha.json", "beta.yaml"]


def write_hub(
    root: Path,
    modules: Sequence[Dict[str, Any]] = tuple(BASE_MODULES),
    rows: Sequence[Sequence[str]] = tuple(BASE_ROWS),
    seeds: Iterable[str] = tuple(BASE_SEEDS),
    header: str = "id\tmodule\tconsumer_assets\treason",
) -> Path:
    """Build a hermetic hub tree: catalog + mandatory registry + shipped seeds."""
    (root / "catalog" / "modules").mkdir(parents=True, exist_ok=True)
    (root / "templates" / "module").mkdir(parents=True, exist_ok=True)
    (root / "guardrails").mkdir(parents=True, exist_ok=True)
    for entry in modules:
        directory = root / "catalog" / "modules" / entry["dir"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "module.json").write_text(
            json.dumps(entry["manifest"], indent=2) + "\n", encoding="utf-8"
        )
    lines = ["# synthetic mandatory registry", header]
    lines.extend("\t".join(row) for row in rows)
    (root / "catalog" / "mandatory.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for asset in seeds:
        seed = root / "templates" / "module" / asset
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text("{}\n" if seed.suffix == ".json" else "seed\n", encoding="utf-8")
    return root


def write_repo(root: Path, register: Sequence[Dict[str, Any]] | None = None) -> Path:
    """A scratch consumer repo: its own manifest, its register, its .gitmodules."""
    root.mkdir(parents=True, exist_ok=True)
    entries = list(register) if register is not None else list(DEFAULT_REGISTER)
    (root / "module.json").write_text(
        json.dumps({"schema": "cmr.module/v1", "id": "probe-repo", "submodules": entries}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (root / ".gitmodules").write_text(
        '[submodule "vendor/CMR"]\n\tpath = vendor/CMR\n\turl = https://github.com/kushin77/CMR.git\n',
        encoding="utf-8",
    )
    return root


#: ``delta`` filed a request; ``epsilon`` *claims* it has been admitted. Neither
#: is in the synthetic catalog, so both are refused — a claim is not membership.
DEFAULT_REGISTER: List[Dict[str, Any]] = [
    {
        "id": "delta",
        "repo": "kushin77/delta",
        "admission": "requested",
        "request": "kushin77/delta#1",
    },
    {
        "id": "epsilon",
        "repo": "kushin77/epsilon",
        "admission": "declared",
        "request": "kushin77/epsilon#2",
    },
]

#: A target that is already registered (must NOT be target-pending), a target
#: that is not (must be), and a watch question the hub has not answered.
BASE_TARGETS: Dict[str, Any] = {
    "schema": "ao.module-targets/v1",
    "targets": [
        {"id": "alpha", "repo": "kushin77/alpha", "blocking": ["kushin77/CMR#1"]},
        {
            "id": "zeta",
            "repo": "kushin77/zeta",
            "blocking": ["kushin77/CMR#435", "kushin77/zeta#1"],
            "onboarding": "CMR:ONBOARD-0009",
        },
    ],
    "watch": [{"id": "eta", "hub_issue": "kushin77/CMR#765", "disposition": "becoming-a-module"}],
}


def write_targets(path: Path, data: Dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data if data is not None else BASE_TARGETS, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def hub(tmp_path: Path) -> Path:
    return write_hub(tmp_path / "hub")


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    return write_repo(tmp_path / "repo")


@pytest.fixture
def targets(tmp_path: Path) -> Path:
    return write_targets(tmp_path / "targets.json")


@pytest.fixture
def make_hub(tmp_path: Path):
    """Factory: ``make_hub("case", modules=…, rows=…, seeds=…)`` -> a fresh hub."""

    def factory(name: str = "hub", **kw: Any) -> Path:
        return write_hub(tmp_path / name, **kw)

    return factory


@pytest.fixture
def built(hub: Path, consumer: Path, targets: Path):
    """The clean registry document over the synthetic surfaces."""
    from governance.modules import registry

    return registry.build(consumer, hub, targets)


def codes(refusals) -> List[str]:
    """``["CODE: subject", …]`` — what a gate greps for."""
    return ["{}: {}".format(finding.code, finding.subject) for finding in refusals]


def refusal_subjects(doc: Dict[str, Any], code: str) -> List[str]:
    return sorted(
        finding["subject"] for finding in doc["refusals"] if finding["code"] == code
    )
