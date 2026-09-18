"""Tests for the per-surface solution-class check (issue #351).

Every finding code the checker can emit is provoked here at least once, so a
code that stops being reachable is a test failure rather than a silent hole.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE.parent
ROOT = PKG_DIR.parent.parent

import surfaces  # noqa: E402  (conftest puts PKG_DIR on sys.path)
from checker import load_policy  # noqa: E402
from surfaces import (  # noqa: E402
    CODE_BELOW,
    CODE_DUPLICATE,
    CODE_MANUAL,
    CODE_PATH_ESCAPES,
    CODE_PATH_MISSING,
    CODE_UNDECLARED,
    CODE_UNKNOWN,
    EVIDENCE_KEYS,
    KIND_MACHINE,
    KIND_MANUAL,
    SurfacePolicy,
    SurfacePolicyUnavailable,
    SurfaceSpec,
    evaluate_surfaces,
    load_surface_policy,
    main,
    measure_gate,
    measure_path_evidence,
)

REAL_POLICY = PKG_DIR / "surfaces.yaml"
ISSUE_POLICY = PKG_DIR / "policy.yaml"


def codes(findings) -> set:
    return {finding.code for finding in findings}


def errors(findings) -> list:
    return [f for f in findings if f.severity == "error"]


# -- helpers ------------------------------------------------------------------


def base_requirements() -> dict:
    return {
        "template": [],
        "class": ["tests"],
        "pattern": ["tests", "contract"],
        "enterprise": ["tests", "contract", "controls", "audit", "schema", "rollback"],
        "faang": [
            "tests",
            "contract",
            "controls",
            "audit",
            "schema",
            "rollback",
            "gate",
        ],
        "elite": [
            "tests",
            "contract",
            "controls",
            "audit",
            "schema",
            "rollback",
            "gate",
            "live_sync",
        ],
    }


def policy_doc(**overrides) -> dict:
    doc = {
        "schema": "cmr.surface-class/policy-v1",
        "ladder": ["template", "class", "pattern", "enterprise", "faang", "elite"],
        "evidence": {
            name: {
                "kind": (KIND_MANUAL if name == "rollback" else KIND_MACHINE),
                "description": name,
            }
            for name in EVIDENCE_KEYS
        },
        "requirements": base_requirements(),
        "surface_roots": [],
        "waived_roots": [],
        "surfaces": [],
    }
    doc.update(overrides)
    return doc


def write_policy(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "surfaces.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def make_surface(root: Path, rel: str, **flags) -> Path:
    base = root / rel
    base.mkdir(parents=True, exist_ok=True)
    if flags.get("readme"):
        (base / "README.md").write_text("# contract\n", encoding="utf-8")
    if flags.get("tests"):
        (base / "tests").mkdir(exist_ok=True)
        (base / "tests" / "test_feature.py").write_text("", encoding="utf-8")
    if flags.get("controls"):
        (base / "policy-controls.yaml").write_text("controls: []\n", encoding="utf-8")
    if flags.get("audit"):
        (base / "auditlog.py").write_text("", encoding="utf-8")
    if flags.get("schema"):
        (base / "thing.schema.json").write_text("{}\n", encoding="utf-8")
    if flags.get("live"):
        (base / "live_feed.py").write_text("", encoding="utf-8")
    return base


def policy_with(**overrides) -> SurfacePolicy:
    doc = policy_doc(**overrides)
    return SurfacePolicy(
        ladder=tuple(doc["ladder"]),
        evidence={
            name: surfaces.Evidence(
                name=name, kind=spec["kind"], description=spec["description"]
            )
            for name, spec in doc["evidence"].items()
        },
        requirements={
            rung: tuple(names) for rung, names in doc["requirements"].items()
        },
        surfaces=tuple(doc["surfaces"]),
        surface_roots=tuple(doc["surface_roots"]),
        waived_roots=tuple(doc["waived_roots"]),
    )


# -- the real policy on the real tree ----------------------------------------


def test_real_policy_loads():
    policy = load_surface_policy(REAL_POLICY)
    assert policy.ladder == ("template", "class", "pattern", "enterprise", "faang", "elite")
    # The five #351 rows are the floor of the declared set; #590/#620/#885 and
    # later lanes add rows, so this is a superset check, not an exact one.
    assert {spec.surface for spec in policy.surfaces} >= {
        "shell",
        "portal",
        "gateway",
        "telemetry",
        "registry",
    }


def test_ladder_is_identical_to_the_issue_policy():
    surface_policy = load_surface_policy(REAL_POLICY)
    issue_policy = load_policy(ISSUE_POLICY)
    assert surface_policy.ladder == issue_policy.ladder


def test_real_tree_declarations_match_measurement():
    policy = load_surface_policy(REAL_POLICY)
    rows, findings = evaluate_surfaces(policy, ROOT)
    assert len(rows) == len(policy.surfaces)
    for row in rows:
        assert row.declared_class == row.measured_class, (
            "surface %s declares %s but measures %s"
            % (row.surface, row.declared_class, row.measured_class)
        )
    assert errors(findings) == []


def test_real_tree_manual_requirements_are_reported():
    policy = load_surface_policy(REAL_POLICY)
    _, findings = evaluate_surfaces(policy, ROOT)
    manual = [f for f in findings if f.code == CODE_MANUAL]
    # gateway, telemetry and registry declare a class that requires `rollback`;
    # a manual requirement is reported, never silently assumed met.
    # Later rows (#590/#620/#885) also declare `enterprise`+ and are reported
    # too, so this is a superset check.
    assert {f.subject for f in manual} >= {"gateway", "telemetry", "registry"}


# -- evidence measurement -----------------------------------------------------


def test_measure_path_evidence_detects_each_signal(tmp_path):
    make_surface(
        tmp_path,
        "thing",
        readme=True,
        tests=True,
        controls=True,
        audit=True,
        schema=True,
        live=True,
    )
    evidence = measure_path_evidence(tmp_path, "thing")
    assert evidence == {
        "contract": True,
        "tests": True,
        "controls": True,
        "audit": True,
        "schema": True,
        "live_sync": True,
    }


def test_measure_path_evidence_ignores_test_artifacts(tmp_path):
    base = make_surface(tmp_path, "thing")
    (base / "tests").mkdir(exist_ok=True)
    (base / "tests" / "audit.schema.json").write_text("{}\n", encoding="utf-8")
    (base / "tests" / "policy.yaml").write_text("x: 1\n", encoding="utf-8")
    evidence = measure_path_evidence(tmp_path, "thing")
    assert evidence["audit"] is False
    assert evidence["schema"] is False
    assert evidence["controls"] is False


def test_measure_gate_matches_the_surface_name(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "check-registry-parity.sh").write_text("", encoding="utf-8")
    assert measure_gate(tmp_path, "registry") is True
    assert measure_gate(tmp_path, "gateway") is False


# -- findings -----------------------------------------------------------------


def test_surface_below_declared_class(tmp_path):
    make_surface(tmp_path, "thing", readme=True)  # contract only, no tests
    doc = policy_doc(
        surfaces=[
            {"surface": "thing", "path": "thing", "declared_class": "pattern"}
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    rows, findings = evaluate_surfaces(policy, tmp_path)
    assert rows[0].measured_class == "template"
    below = [f for f in findings if f.code == CODE_BELOW]
    assert below and below[0].subject == "thing"
    assert "tests" in below[0].message


def test_surface_path_missing(tmp_path):
    doc = policy_doc(
        surfaces=[
            {"surface": "ghost", "path": "no/such/dir", "declared_class": "template"}
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert codes(findings) == {CODE_PATH_MISSING}


def test_surface_path_escapes_root(tmp_path):
    doc = policy_doc(
        surfaces=[
            {"surface": "evil", "path": "../elsewhere", "declared_class": "template"}
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert codes(findings) == {CODE_PATH_ESCAPES}


def test_surface_undeclared_root(tmp_path):
    (tmp_path / "portal").mkdir()
    doc = policy_doc(surface_roots=["portal"], surfaces=[])
    doc["surfaces"] = [
        {"surface": "other", "path": "other", "declared_class": "template"}
    ]
    (tmp_path / "other").mkdir()
    policy = load_surface_policy(write_policy(tmp_path, doc))
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert CODE_UNDECLARED in codes(findings)


def test_declared_root_is_not_flagged(tmp_path):
    (tmp_path / "portal").mkdir()
    doc = policy_doc(
        surface_roots=["portal"],
        surfaces=[
            {"surface": "portal", "path": "portal", "declared_class": "template"}
        ],
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert errors(findings) == []


def test_surface_duplicate(tmp_path):
    make_surface(tmp_path, "thing")
    policy = policy_with(
        surfaces=[
            SurfaceSpec(surface="thing", path="thing", declared_class="template"),
            SurfaceSpec(surface="thing", path="thing", declared_class="template"),
        ]
    )
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert CODE_DUPLICATE in codes(findings)


def test_surface_unknown_class(tmp_path):
    make_surface(tmp_path, "thing")
    policy = policy_with(
        surfaces=[
            SurfaceSpec(surface="thing", path="thing", declared_class="platinum")
        ]
    )
    _, findings = evaluate_surfaces(policy, tmp_path)
    unknown = [f for f in findings if f.code == CODE_UNKNOWN]
    assert unknown and "platinum" in unknown[0].message


def test_manual_requirement_is_reported_not_failed(tmp_path):
    make_surface(
        tmp_path, "thing", readme=True, tests=True, controls=True, audit=True, schema=True
    )
    doc = policy_doc(
        surfaces=[
            {"surface": "thing", "path": "thing", "declared_class": "enterprise"}
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    rows, findings = evaluate_surfaces(policy, tmp_path)
    assert rows[0].measured_class == "enterprise"
    assert errors(findings) == []
    manual = [f for f in findings if f.code == CODE_MANUAL]
    assert manual and manual[0].subject == "thing"
    assert "rollback" in manual[0].message


# -- policy validation --------------------------------------------------------


def test_policy_rejects_missing_evidence_block(tmp_path):
    doc = policy_doc()
    del doc["evidence"]
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_unknown_evidence_key(tmp_path):
    doc = policy_doc()
    doc["evidence"]["mystery"] = {"kind": "machine"}
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_bad_evidence_kind(tmp_path):
    doc = policy_doc()
    doc["evidence"]["contract"]["kind"] = "vibes"
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_non_cumulative_requirements(tmp_path):
    doc = policy_doc()
    doc["requirements"]["pattern"] = ["contract"]  # drops `tests`
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_unknown_rung_in_requirements(tmp_path):
    doc = policy_doc()
    doc["requirements"]["platinum"] = []
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_unknown_declared_class(tmp_path):
    doc = policy_doc(
        surfaces=[{"surface": "thing", "path": "thing", "declared_class": "nope"}]
    )
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_bad_surface_root(tmp_path):
    doc = policy_doc(surface_roots=["a/b"])
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(write_policy(tmp_path, doc))


def test_policy_rejects_unreadable_file(tmp_path):
    with pytest.raises(SurfacePolicyUnavailable):
        load_surface_policy(tmp_path / "missing.yaml")


# -- CLI ----------------------------------------------------------------------


def test_cli_ok_on_a_conforming_surface(tmp_path):
    make_surface(tmp_path, "thing", readme=True, tests=True)
    doc = policy_doc(
        surfaces=[{"surface": "thing", "path": "thing", "declared_class": "pattern"}]
    )
    policy_path = write_policy(tmp_path, doc)
    assert main(["check", "--root", str(tmp_path), "--policy", str(policy_path)]) == 0


def test_cli_not_ok_below_class(tmp_path, capsys):
    make_surface(tmp_path, "thing")
    doc = policy_doc(
        surfaces=[{"surface": "thing", "path": "thing", "declared_class": "class"}]
    )
    policy_path = write_policy(tmp_path, doc)
    rc = main(["check", "--root", str(tmp_path), "--policy", str(policy_path)])
    assert rc == 1
    assert "surface-class: FAIL" in capsys.readouterr().err


def test_cli_cannot_assess_on_a_missing_policy(tmp_path, capsys):
    assert main(["check", "--root", str(tmp_path), "--policy", str(tmp_path / "nope.yaml")]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_cli_json_report(tmp_path, capsys):
    make_surface(tmp_path, "thing", readme=True, tests=True)
    doc = policy_doc(
        surfaces=[{"surface": "thing", "path": "thing", "declared_class": "pattern"}]
    )
    policy_path = write_policy(tmp_path, doc)
    rc = main(
        ["check", "--root", str(tmp_path), "--policy", str(policy_path), "--json"]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["surfaces"][0]["measured_class"] == "pattern"


def test_surface_spec_is_replaceable():
    spec = SurfaceSpec(surface="a", path="b", declared_class="template")
    assert replace(spec, declared_class="class").declared_class == "class"
