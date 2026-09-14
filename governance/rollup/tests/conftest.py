"""Pytest bootstrap + fixtures for the governance/rollup suite (issue #151).

The modules under ``governance/rollup/`` are standalone files with no package
``__init__.py`` (the convention shared with governance/board, conformance,
lessons and knowledge), so the package directory goes on ``sys.path`` and the
tests import them plainly.

Every fixture here is built from scratch in ``tmp_path``: a test that depends on
a checked-in input would pass for the wrong reason the moment someone edits
that input. The committed pilot and gate fixtures are exercised too, but as
*subjects* of assertions, never as shared mutable state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import pytest

ROLLUP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ROLLUP_DIR.parents[1]
if str(ROLLUP_DIR) not in sys.path:
    sys.path.insert(0, str(ROLLUP_DIR))

ORG_SCHEMA = "ao.rollup/org-v1"
INVENTORY_SCHEMA = "ao.rollup/fleet-inventory-v1"


def sme(**overrides: Any) -> Dict[str, Any]:
    """One SME fact; each test overrides only the field it is about."""
    base: Dict[str, Any] = {
        "id": "qa-sme",
        "persona_tenant": "platform",
        "weekly_spend_ceiling_usd": 100.0,
        "spend_usd": 40.0,
        "capacity_hours": 20.0,
        "engaged_hours": 10.0,
        "dispatched": 4,
        "closed": 3,
    }
    base.update(overrides)
    return base


def inventory(
    repo: str,
    tenant: str,
    smes: Iterable[Mapping[str, Any]],
    *,
    checks: int = 4,
    findings: int = 0,
    source: str = "declared",
    start: str = "2026-09-07",
    end: str = "2026-09-13",
) -> Dict[str, Any]:
    return {
        "schema": INVENTORY_SCHEMA,
        "repo": repo,
        "tenant": tenant,
        "source": source,
        "window": {"start": start, "end": end},
        "smes": [dict(item) for item in smes],
        "drift": {"checks": checks, "findings": findings},
    }


def org(
    tenants: Mapping[str, Any],
    *,
    enterprise_id: str = "fixture-ent",
    ceiling: float = 1000.0,
    source: str = "declared",
) -> Dict[str, Any]:
    return {
        "schema": ORG_SCHEMA,
        "source": source,
        "generated_at": "2026-09-13",
        "enterprise": {
            "id": enterprise_id,
            "name": "Fixture enterprise",
            "weekly_spend_ceiling_usd": ceiling,
        },
        "tenants": [
            {
                "id": tid,
                "name": spec.get("name", tid.title()),
                "weekly_spend_ceiling_usd": spec["ceiling"],
                "repos": list(spec["repos"]),
            }
            for tid, spec in tenants.items()
        ],
    }


def write_tree(
    root: Path,
    org_doc: Mapping[str, Any],
    inventories: Iterable[Mapping[str, Any]],
    *,
    inventory_dir: Optional[Path] = None,
) -> Path:
    """Write an org + inventory set into ``root`` and return the org path."""
    import yaml

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    org_path = root / "org.yaml"
    org_path.write_text(yaml.safe_dump(dict(org_doc), sort_keys=False), encoding="utf-8")
    target = inventory_dir if inventory_dir is not None else root / "inventory"
    target.mkdir(parents=True, exist_ok=True)
    for doc in inventories:
        name = str(doc["repo"]).replace("/", "-") + ".yaml"
        (target / name).write_text(yaml.safe_dump(dict(doc), sort_keys=False), encoding="utf-8")
    return org_path


def run_cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the CLI the way an operator (and the gate) does.

    ``start_new_session`` keeps a sibling lane's interrupt from landing in the
    child, and bytecode writing is off so a "wrote nothing" assertion cannot be
    tripped by the interpreter's own cache.
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(ROLLUP_DIR / "cli.py"), *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        start_new_session=True,
    )


def tree_snapshot(root: Path) -> Dict[str, Any]:
    """Every file under ``root`` with its size, mtime and content hash."""
    import hashlib

    snapshot: Dict[str, Any] = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_dir():
            snapshot[str(path.relative_to(root)) + "/"] = {"dir": True}
            continue
        data = path.read_bytes()
        stat = path.stat()
        snapshot[str(path.relative_to(root))] = {
            "size": len(data),
            "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return snapshot


def build_report(root: Path, org_doc: Mapping[str, Any], inventories: Iterable[Mapping[str, Any]]):
    """Load a written tree through the real loader and project it.

    Goes through the same path the CLI uses (schema validation included), so a
    test cannot accidentally bypass the contract by constructing facts directly.
    """
    from inputs import load_inputs, with_problems
    from model import project

    org_path = write_tree(root, org_doc, inventories)
    loaded = load_inputs(org_path, Path(root) / "inventory", ROLLUP_DIR / "schema.yaml")
    assert loaded.org is not None, loaded.problems
    report = project(
        loaded.org,
        loaded.fleets,
        inputs=loaded.inputs,
        schema_path=loaded.schema_path,
        schema_digest=loaded.schema_digest,
    )
    return with_problems(report, loaded.problems)


@pytest.fixture()
def pilot_org() -> Path:
    return ROLLUP_DIR / "pilot" / "org.yaml"


@pytest.fixture()
def pilot_inventory_dir() -> Path:
    return ROLLUP_DIR / "pilot" / "inventory"


@pytest.fixture()
def schema_path() -> Path:
    return ROLLUP_DIR / "schema.yaml"
