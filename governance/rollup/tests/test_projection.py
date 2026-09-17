"""Projection-only proofs: the roll-up derives, it does not persist (issue #151).

The acceptance criterion is that the enterprise view is a **projection, never a
second source of truth**. That is a property of the running code, so it is
tested three ways rather than asserted in prose:

* the input files are byte-identical after a run (content hashes + mtimes);
* the engine opens no file for writing while it projects, proven by wrapping
  ``io.open`` rather than by reading the source;
* running the CLI leaves both the input tree and the package directory exactly
  as they were — no report file, no cache, no state.
"""

from __future__ import annotations

import io
from dataclasses import FrozenInstanceError

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
    "governance_rollup_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)

# Persistent handle for the lazy in-function "model" imports below (see
# governance/rollup/tests/conftest.py for the rationale: issues #699, #702,
# #1042).
import model as _rollup_model  # noqa: E402
ROLLUP_DIR = _conftest.ROLLUP_DIR
build_report = _conftest.build_report
inventory = _conftest.inventory
org = _conftest.org
run_cli = _conftest.run_cli
sme = _conftest.sme
tree_snapshot = _conftest.tree_snapshot

TENANT = {"alpha": {"ceiling": 300.0, "repos": ["fx/one"]}}


def docs():
    return (
        org(TENANT, ceiling=500.0),
        [
            inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=140.0)]),
        ],
    )


def test_the_input_tree_is_byte_identical_after_a_projection(tmp_path):
    org_doc, inv = docs()
    build_report(tmp_path, org_doc, inv)  # write the tree once
    before = tree_snapshot(tmp_path)

    from inputs import load_inputs, with_problems
    project = _rollup_model.project

    loaded = load_inputs(tmp_path / "org.yaml", tmp_path / "inventory", ROLLUP_DIR / "schema.yaml")
    assert loaded.org is not None
    report = with_problems(
        project(loaded.org, loaded.fleets, inputs=loaded.inputs), loaded.problems
    )

    assert report.exit_code in (0, 1, 2)
    assert tree_snapshot(tmp_path) == before


def test_projection_opens_no_file_for_writing(tmp_path, monkeypatch):
    """A write-mode open anywhere in the projection is a failure."""
    from inputs import load_inputs
    project = _rollup_model.project

    org_doc, inv = docs()
    build_report(tmp_path, org_doc, inv)
    loaded = load_inputs(tmp_path / "org.yaml", tmp_path / "inventory", ROLLUP_DIR / "schema.yaml")
    assert loaded.org is not None

    opened: list = []
    real_open = io.open

    def spy(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            opened.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", spy)
    report = project(loaded.org, loaded.fleets, inputs=loaded.inputs)
    monkeypatch.undo()

    assert opened == []
    assert report.enterprise.spend.total_usd == 140.0


def test_the_cli_writes_neither_into_the_inputs_nor_into_the_package(tmp_path):
    org_doc, inv = docs()
    build_report(tmp_path, org_doc, inv)
    inputs_before = tree_snapshot(tmp_path)
    package_before = sorted(str(p.relative_to(ROLLUP_DIR)) for p in ROLLUP_DIR.rglob("*"))

    result = run_cli(
        tmp_path,
        "project",
        "--org",
        "org.yaml",
        "--inventory-dir",
        "inventory",
        "--schema",
        str(ROLLUP_DIR / "schema.yaml"),
        "--json",
    )

    assert result.returncode == 1, result.stderr  # this tree is over its ceiling
    assert tree_snapshot(tmp_path) == inputs_before
    assert sorted(str(p.relative_to(ROLLUP_DIR)) for p in ROLLUP_DIR.rglob("*")) == package_before
    assert not (tmp_path / ".verify").exists()


def test_the_report_says_it_is_a_projection_and_names_its_sources(tmp_path):
    org_doc, inv = docs()
    report = build_report(tmp_path, org_doc, inv)
    payload = report.to_dict()

    assert payload["projection"] is True
    assert payload["persisted"] is False
    assert payload["generated_from"], "a projection must name what it was derived from"
    assert any("org" in line for line in payload["generated_from"])
    kinds = {entry["kind"] for entry in payload["inputs"]}
    assert kinds == {"schema", "org", "inventory"}


def test_every_input_travels_with_its_content_hash(tmp_path):
    org_doc, inv = docs()
    report = build_report(tmp_path, org_doc, inv)

    digests = {entry["kind"]: entry["sha256"] for entry in report.to_dict()["inputs"]}
    assert digests["org"] == report.org.digest
    assert digests["inventory"] == report.fleets[0].digest

    # The digest is the file's, so editing the file changes what the report says
    # it was derived from — a projection stays traceable to its bytes.
    changed = tmp_path / "changed"
    changed.mkdir()
    other = build_report(
        changed,
        org(TENANT, ceiling=500.0),
        [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=100.0, spend_usd=50.0)])],
    )
    assert other.fleets[0].digest != report.fleets[0].digest
    assert other.enterprise.spend.total_usd != report.enterprise.spend.total_usd


def test_projection_is_reproducible_and_the_facts_stay_frozen(tmp_path):
    from inputs import load_inputs
    project = _rollup_model.project

    org_doc, inv = docs()
    build_report(tmp_path, org_doc, inv)
    loaded = load_inputs(tmp_path / "org.yaml", tmp_path / "inventory", ROLLUP_DIR / "schema.yaml")
    assert loaded.org is not None

    first = project(loaded.org, loaded.fleets, inputs=loaded.inputs)
    second = project(loaded.org, loaded.fleets, inputs=loaded.inputs)
    assert first == second
    assert first.to_json() == second.to_json()

    fact = loaded.fleets[0].smes[0]
    with pytest.raises(FrozenInstanceError):
        fact.spend_usd = 0.0
    with pytest.raises(FrozenInstanceError):
        loaded.org.enterprise_id = "somewhere-else"
    assert fact.spend_usd == 140.0


def test_the_committed_pilot_projects_from_its_own_declarations(tmp_path, pilot_org,
                                                               pilot_inventory_dir, schema_path):
    """The pilot is a real projection too, not a special case in the code."""
    from inputs import load_inputs
    project = _rollup_model.project

    before = tree_snapshot(ROLLUP_DIR / "pilot")
    loaded = load_inputs(pilot_org, pilot_inventory_dir, schema_path)
    assert loaded.problems == ()
    assert loaded.org is not None
    report = project(
        loaded.org,
        loaded.fleets,
        inputs=loaded.inputs,
        schema_path=loaded.schema_path,
        schema_digest=loaded.schema_digest,
    )

    assert report.status == "ok"
    assert len(report.enterprise.tenants) == len(loaded.org.tenants)
    assert report.enterprise.spend.total_usd == sum(
        sum(s.spend_usd for s in fleet.smes) for fleet in loaded.fleets
    )
    assert tree_snapshot(ROLLUP_DIR / "pilot") == before
