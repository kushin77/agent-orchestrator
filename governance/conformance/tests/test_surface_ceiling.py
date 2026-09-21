"""Tests for the class ceiling and the module-level declared class (issue #883).

Two additions to the per-surface check (ADR-0031):

* ``class_ceiling`` — a surface row may declare the highest rung its shape can
  honestly reach, with a reason. The ceiling is REPORTED on every run, never
  silently waived; a declaration above it is refused by name, and a ceiling the
  evidence has already exceeded is refused as stale.
* ``module.json`` ``solution_class`` — the module declares its own class, and it
  may never exceed the floor: the lowest measured class over the *product*
  surfaces (every row without a ceiling). A synthetic-policy mutant declaring
  above the floor is refused by name (``test_cli_refuses_a_mutant_manifest``,
  ``test_cli_module_flag_overrides_the_manifest_path``).

Both checks stay silent when their input is absent (no ceiling key, no
manifest), so the existing suite's exact finding-code assertions still hold.

EPIC #878 flip note: that flip put every product surface at `elite`, the
ladder's top rung, so the above-floor negative control could no longer be run
against the real tree (there was no rung left to mutate into) and it was
replaced by a real-tree pin. Issue #1256 declared the five module roots at the
rungs their evidence measures, which puts the floor back at `pattern` and makes
the real-tree control expressible again — so the pin is gone and
``test_real_tree_mutant_manifest_declaring_elite_is_refused_by_name`` now
PROVOKES the refusal in the real policy instead of restating the floor. The
synthetic-policy controls named above stay as they were.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE.parent
ROOT = PKG_DIR.parent.parent

from surfaces import (  # noqa: E402  (conftest puts PKG_DIR on sys.path)
    CODE_ABOVE_CEILING,
    CODE_BELOW,
    CODE_CEILING,
    CODE_CEILING_STALE,
    CODE_MODULE_ABOVE_FLOOR,
    CODE_MODULE_UNDECLARED,
    CODE_MODULE_UNKNOWN,
    CODE_MODULE_UNREADABLE,
    EVIDENCE_KEYS,
    KIND_MACHINE,
    KIND_MANUAL,
    MODULE_CLASS_KEY,
    MODULE_RELPATH,
    SurfacePolicyUnavailable,
    evaluate_module_class,
    evaluate_surfaces,
    load_surface_policy,
    main,
    product_floor,
)

REAL_POLICY = PKG_DIR / "surfaces.yaml"
REAL_MODULE = ROOT / "module.json"


# -- helpers (local copies of test_surfaces.py's, so this module does not
# depend on a cross-test import that a collection-mode change would break) ---


def codes(findings) -> set:
    return {finding.code for finding in findings}


def errors(findings) -> list:
    return [f for f in findings if f.severity == "error"]


def base_requirements() -> dict:
    lower = ["tests", "contract", "controls", "audit", "schema", "rollback"]
    return {
        "template": [],
        "class": ["tests"],
        "pattern": ["tests", "contract"],
        "enterprise": list(lower),
        "faang": lower + ["gate"],
        "elite": lower + ["gate", "live_sync"],
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
        # A waiver is a name -> reason mapping (issue #1256).
        "waived_roots": {},
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
    return base


def surface_row(name: str, path: str, declared: str, **extra) -> dict:
    row = {"surface": name, "path": path, "declared_class": declared}
    row.update(extra)
    return row


def write_module(root: Path, declared) -> Path:
    path = root / MODULE_RELPATH
    path.write_text(
        json.dumps({"schema": "cmr.module/v1", "id": "x", MODULE_CLASS_KEY: declared}),
        encoding="utf-8",
    )
    return path


# -- policy shape -------------------------------------------------------------


def test_ceiling_must_be_a_rung(tmp_path):
    doc = policy_doc(
        surfaces=[surface_row("s", "s", "template", class_ceiling="gold", ceiling_reason="x")]
    )
    with pytest.raises(SurfacePolicyUnavailable, match="class_ceiling"):
        load_surface_policy(write_policy(tmp_path, doc))


def test_ceiling_requires_a_reason(tmp_path):
    doc = policy_doc(surfaces=[surface_row("s", "s", "template", class_ceiling="pattern")])
    with pytest.raises(SurfacePolicyUnavailable, match="ceiling_reason"):
        load_surface_policy(write_policy(tmp_path, doc))


def test_ceiling_at_the_top_rung_is_meaningless(tmp_path):
    doc = policy_doc(
        surfaces=[surface_row("s", "s", "template", class_ceiling="elite", ceiling_reason="x")]
    )
    with pytest.raises(SurfacePolicyUnavailable, match="top rung"):
        load_surface_policy(write_policy(tmp_path, doc))


def test_ceiling_is_loaded_onto_the_spec(tmp_path):
    doc = policy_doc(
        surfaces=[
            surface_row("s", "s", "template", class_ceiling="pattern", ceiling_reason="static")
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    assert policy.surfaces[0].class_ceiling == "pattern"
    assert policy.surfaces[0].ceiling_reason == "static"


# -- ceiling evaluation -------------------------------------------------------


def test_ceiling_is_reported_on_every_run_never_silently_waived(tmp_path):
    make_surface(tmp_path, "s")
    doc = policy_doc(
        surfaces=[
            surface_row("s", "s", "template", class_ceiling="pattern", ceiling_reason="static")
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    rows, findings = evaluate_surfaces(policy, tmp_path)
    assert codes(findings) == {CODE_CEILING}
    assert not errors(findings)
    assert rows[0].class_ceiling == "pattern"
    assert rows[0].as_dict()["class_ceiling"] == "pattern"
    [report] = findings
    assert "static" in report.message and "'s'" in report.message


def test_no_ceiling_key_means_no_ceiling_finding(tmp_path):
    make_surface(tmp_path, "s")
    policy = load_surface_policy(
        write_policy(tmp_path, policy_doc(surfaces=[surface_row("s", "s", "template")]))
    )
    rows, findings = evaluate_surfaces(policy, tmp_path)
    assert findings == []
    assert rows[0].class_ceiling == ""
    assert rows[0].as_dict()["class_ceiling"] == ""


def test_declaring_above_the_ceiling_is_refused_by_name(tmp_path):
    make_surface(tmp_path, "s", readme=True, tests=True)
    doc = policy_doc(
        surfaces=[
            surface_row("s", "s", "pattern", class_ceiling="class", ceiling_reason="static")
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    rows, findings = evaluate_surfaces(policy, tmp_path)
    # The evidence supports `pattern`, so this is purely the ceiling refusing.
    assert rows[0].measured_class == "pattern"
    assert CODE_ABOVE_CEILING in codes(findings)
    assert CODE_BELOW not in codes(findings)
    [above] = [f for f in findings if f.code == CODE_ABOVE_CEILING]
    assert above.severity == "error"
    assert "'s'" in above.message and "'class'" in above.message


def test_a_ceiling_the_evidence_exceeds_is_refused_as_stale(tmp_path):
    make_surface(tmp_path, "s", readme=True, tests=True)
    doc = policy_doc(
        surfaces=[
            surface_row("s", "s", "template", class_ceiling="class", ceiling_reason="static")
        ]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert CODE_CEILING_STALE in codes(findings)
    [stale] = [f for f in findings if f.code == CODE_CEILING_STALE]
    assert stale.severity == "error"
    assert "'s'" in stale.message


def test_below_declared_still_fires_under_a_ceiling(tmp_path):
    # scripts/check-surface-class.sh mutates the FIRST row (a ceiling row) one
    # rung up and greps for CODE_BELOW; the ceiling must not swallow that.
    make_surface(tmp_path, "s")
    doc = policy_doc(
        surfaces=[surface_row("s", "s", "class", class_ceiling="pattern", ceiling_reason="x")]
    )
    policy = load_surface_policy(write_policy(tmp_path, doc))
    _, findings = evaluate_surfaces(policy, tmp_path)
    assert CODE_BELOW in codes(findings)


# -- module declared class ----------------------------------------------------


def two_surface_policy(tmp_path: Path):
    make_surface(tmp_path, "prod", readme=True, tests=True)  # measures `pattern`
    make_surface(tmp_path, "static")  # measures `template`, ceiling row
    doc = policy_doc(
        surfaces=[
            surface_row("prod", "prod", "pattern"),
            surface_row(
                "static", "static", "template", class_ceiling="class", ceiling_reason="x"
            ),
        ]
    )
    return load_surface_policy(write_policy(tmp_path, doc))


def test_product_floor_excludes_ceiling_rows(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    floor, holders = product_floor(policy, rows)
    assert floor == "pattern"
    assert holders == ("prod",)


def test_missing_manifest_is_silent(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    assert evaluate_module_class(policy, rows, tmp_path / MODULE_RELPATH) == []


def test_module_at_or_below_the_floor_is_accepted(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    for declared in ("template", "class", "pattern"):
        path = write_module(tmp_path, declared)
        assert evaluate_module_class(policy, rows, path) == [], declared


def test_mutant_module_declaring_elite_is_refused_by_name(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    path = write_module(tmp_path, "elite")
    findings = evaluate_module_class(policy, rows, path)
    assert codes(findings) == {CODE_MODULE_ABOVE_FLOOR}
    [above] = findings
    assert above.severity == "error"
    assert "'elite'" in above.message
    assert "'pattern'" in above.message
    assert "prod" in above.message


def test_module_without_the_key_is_refused(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    path = tmp_path / MODULE_RELPATH
    path.write_text('{"schema": "cmr.module/v1"}', encoding="utf-8")
    assert codes(evaluate_module_class(policy, rows, path)) == {CODE_MODULE_UNDECLARED}


def test_module_declaring_a_non_rung_is_refused(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    path = write_module(tmp_path, "gold")
    assert codes(evaluate_module_class(policy, rows, path)) == {CODE_MODULE_UNKNOWN}


def test_unreadable_manifest_is_refused(tmp_path):
    policy = two_surface_policy(tmp_path)
    rows, _ = evaluate_surfaces(policy, tmp_path)
    path = tmp_path / MODULE_RELPATH
    path.write_text("{not json", encoding="utf-8")
    assert codes(evaluate_module_class(policy, rows, path)) == {CODE_MODULE_UNREADABLE}


# -- CLI ----------------------------------------------------------------------


def test_cli_refuses_a_mutant_manifest(tmp_path, capsys):
    two_surface_policy(tmp_path)
    write_module(tmp_path, "elite")
    rc = main(["check", "--root", str(tmp_path), "--policy", str(tmp_path / "surfaces.yaml")])
    assert rc == 1
    out = capsys.readouterr().out
    assert CODE_MODULE_ABOVE_FLOOR in out


def test_cli_module_flag_overrides_the_manifest_path(tmp_path, capsys):
    two_surface_policy(tmp_path)
    write_module(tmp_path, "pattern")  # the in-root manifest is fine
    mutant = tmp_path / "mutant.json"
    mutant.write_text(json.dumps({MODULE_CLASS_KEY: "elite"}), encoding="utf-8")
    rc = main(
        [
            "check",
            "--root",
            str(tmp_path),
            "--policy",
            str(tmp_path / "surfaces.yaml"),
            "--module",
            str(mutant),
            "--json",
        ]
    )
    assert rc == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["module"]["declared_class"] == "elite"
    assert doc["module"]["floor_class"] == "pattern"
    assert CODE_MODULE_ABOVE_FLOOR in {f["code"] for f in doc["findings"]}


# -- the real tree ------------------------------------------------------------


def test_real_manifest_declares_a_rung_at_or_below_the_product_floor():
    policy = load_surface_policy(REAL_POLICY)
    rows, findings = evaluate_surfaces(policy, ROOT)
    assert not errors(findings)
    module_findings = evaluate_module_class(policy, rows, REAL_MODULE)
    assert module_findings == [], [f.message for f in module_findings]
    declared = json.loads(REAL_MODULE.read_text(encoding="utf-8"))[MODULE_CLASS_KEY]
    floor, _ = product_floor(policy, rows)
    assert policy.rank(declared) <= policy.rank(floor)


def test_real_policy_ceilings_are_the_three_non_product_rows():
    raw = yaml.safe_load(REAL_POLICY.read_text(encoding="utf-8"))
    ceilinged = {
        row["surface"]: row["class_ceiling"]
        for row in raw["surfaces"]
        if "class_ceiling" in row
    }
    assert set(ceilinged) == {"shell", "github", "commit-contract", "edge-cutover"}
    for row in raw["surfaces"]:
        if "class_ceiling" in row:
            assert row["ceiling_reason"].strip(), row["surface"]


def test_real_tree_mutant_manifest_declaring_elite_is_refused_by_name(tmp_path):
    # Restored by issue #1256, and strictly stronger than the pin it replaces:
    # declaring the five module roots at the rungs their evidence measures puts
    # the real product floor back at `pattern`, so the real tree CAN mutate a
    # manifest one rung above the floor again — and the check must refuse it by
    # name. `docs/SURFACE-CLASS.md` has named this test since ADR-0031; the elite
    # floor made that name unwritable for a while, and this is what makes it
    # true again.
    policy = load_surface_policy(REAL_POLICY)
    rows, _ = evaluate_surfaces(policy, ROOT)
    floor, holders = product_floor(policy, rows)
    assert floor == "pattern"
    assert set(holders) >= {"engine", "identity", "control-plane", "fleet"}
    mutant = tmp_path / "module.json"
    mutant.write_text(json.dumps({MODULE_CLASS_KEY: "elite"}), encoding="utf-8")
    findings = evaluate_module_class(policy, rows, mutant)
    assert codes(findings) == {CODE_MODULE_ABOVE_FLOOR}
    [above] = findings
    assert above.severity == "error"
    assert "'elite'" in above.message
    assert "'pattern'" in above.message
    assert "engine" in above.message
    # The real manifest declares exactly the floor, never above it.
    manifest = json.loads(REAL_MODULE.read_text(encoding="utf-8"))
    assert manifest[MODULE_CLASS_KEY] == floor
    assert evaluate_module_class(policy, rows, REAL_MODULE) == []


def test_real_tree_floor_is_below_the_top_rung_so_a_mutant_is_expressible():
    # The precondition the control above depends on, asserted rather than
    # assumed: a floor at the ladder's top rung would make its mutation
    # impossible and the control a formality.
    policy = load_surface_policy(REAL_POLICY)
    rows, _ = evaluate_surfaces(policy, ROOT)
    floor, _ = product_floor(policy, rows)
    assert floor != policy.ladder[-1]
    assert policy.rank(floor) + 1 < len(policy.ladder)
