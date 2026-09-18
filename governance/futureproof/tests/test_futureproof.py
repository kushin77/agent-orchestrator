"""The capstone's suite — every link driven with its provoked half AND its twin.

`scripts/check-futureproof-e2e.sh` runs this. Each test builds a FIXTURE TREE
(never the repository) so a link can be broken deliberately and the verdict
watched to move: a link that passes while doing nothing would otherwise pass
here too, which is exactly the failure the capstone exists to refuse.

The fixture carries the repository's OWN `scripts/discover-checks.sh`, so
`gate-wired` is exercised against the real discovery layer rather than a stub.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "governance" / "futureproof"))

import e2e as E  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _gate_stub(gate: str, rc: int = 0) -> str:
    """A gate that declares a provocation and a verdict token, then exits `rc`."""
    return (
        "#!/usr/bin/env bash\n"
        "# negative control: a mutant of the input must be REFUSED, else FAIL\n"
        "echo 'check-%s: %s'\n"
        "exit %d\n" % (gate, "OK" if rc == 0 else "CANNOT-ASSESS", rc)
    )


def build_tree(tmp_path: Path) -> Path:
    """A complete, all-green fixture: every mechanism wired and assessing."""
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "discover-checks.sh",
                tree / "scripts" / "discover-checks.sh")
    _write(tree / "scripts" / "check-denylist.txt", "# nothing disabled\n")
    for mech in E.MECHANISMS:
        for rel in mech["authorities"]:
            target = tree / rel
            if "." in Path(rel).name:
                _write(target, "declared\n")
            else:
                _write(target / "README.md", "declared\n")
        for gate in mech["gates"]:
            _write(tree / "scripts" / ("check-%s.sh" % gate), _gate_stub(gate))
    return tree


def verdicts(rep: dict) -> dict:
    return {row["mechanism"]: row["verdict"] for row in rep["mechanisms"]}


def run(tree: Path) -> tuple:
    rep = E.report(tree, E.MECHANISMS, timeout=60)
    return rep, E.render(rep)


# -- the clean twin ----------------------------------------------------------

def test_a_complete_tree_is_green(tmp_path):
    rep, (rc, lines) = run(build_tree(tmp_path))
    assert rc == E.OK, "\n".join(lines)
    assert set(verdicts(rep).values()) == {E.ENFORCED}
    assert rep["known_gaps"] == []


# -- link 1: authority-declared ---------------------------------------------

def test_a_missing_authority_is_absent_and_named(tmp_path):
    tree = build_tree(tmp_path)
    (tree / "docs" / "SHELL-PATTERNS.md").unlink()
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert verdicts(rep)["pattern"] == E.ABSENT
    assert "mechanism 'pattern' fails authority-declared" in "\n".join(lines)


# -- link 2: gate-wired ------------------------------------------------------

def test_a_missing_gate_is_refused(tmp_path):
    tree = build_tree(tmp_path)
    (tree / "scripts" / "check-shell-patterns.sh").unlink()
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert verdicts(rep)["pattern"] == E.ABSENT
    assert "gate check-shell-patterns.sh does not exist" in "\n".join(lines)


def test_a_dead_gate_is_refused(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh", "")
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert "is empty (a dead gate)" in "\n".join(lines)


def test_an_undiscovered_gate_is_implemented_ungated(tmp_path):
    # The #1164 class: the control exists and the discovery layer never wires it.
    # Driven with a discovery set that omits the gate, because that is the only
    # way the layer can fail to wire a script whose name matches the convention.
    tree = build_tree(tmp_path)
    discovered = E.discovered_gates(tree) - {"shell-patterns"}
    row = E.assess_mechanism(tree, E.MECHANISMS[1], discovered,
                             E.denylisted_gates(tree), timeout=60)
    assert row["verdict"] == E.IMPLEMENTED_UNGATED
    assert any(h["link"] == E.LINK_WIRED and "is not discovered" in h["detail"]
               for h in row["broken"])


def test_a_gate_under_the_wrong_name_is_absent(tmp_path):
    tree = build_tree(tmp_path)
    (tree / "scripts" / "check-shell-patterns.sh").rename(
        tree / "scripts" / "shell-patterns-check.sh")
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert verdicts(rep)["pattern"] == E.ABSENT
    assert "does not exist" in "\n".join(lines)


def test_a_denylisted_gate_is_refused(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-denylist.txt", "shell-patterns\n")
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert verdicts(rep)["pattern"] == E.IMPLEMENTED_UNGATED
    assert "is denylisted" in "\n".join(lines)


# -- link 3: gate-falsifiable -----------------------------------------------

def test_a_gate_without_a_declared_provocation_is_declared_only(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh",
           "#!/usr/bin/env bash\necho 'check-shell-patterns: OK'\nexit 0\n")
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert verdicts(rep)["pattern"] == E.DECLARED_ONLY
    assert "declares no provocation" in "\n".join(lines)


# -- link 4: assesses-real-tree -- THE AMENDMENT ----------------------------

def test_a_blind_gate_is_declared_only_and_named_by_mechanism(tmp_path):
    """A gate that is permanently CANNOT-ASSESS must not pass with the rest."""
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh", _gate_stub("shell-patterns", rc=2))
    rep, (rc, lines) = run(tree)
    text = "\n".join(lines)
    assert rc == E.NOT_OK
    assert verdicts(rep)["pattern"] == E.DECLARED_ONLY
    assert "mechanism 'pattern' fails assesses-real-tree" in text
    assert "BLIND" in text and "rc 2" in text
    # the other nine mechanisms were still assessed and still hold
    assert sum(1 for v in verdicts(rep).values() if v == E.ENFORCED) == 9


def test_a_gate_that_cannot_run_reaches_no_verdict(tmp_path):
    # A gate that carries the right words but cannot execute must not pass: it
    # reaches no verdict, and `bash` answers rc 2 for a syntax error.
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh",
           "#!/usr/bin/env bash\n"
           "# negative control: a mutant must be REFUSED, else FAIL\n"
           "if then fi\n")
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert "mechanism 'pattern' fails assesses-real-tree" in "\n".join(lines)


def test_a_real_gate_that_assesses_is_green(tmp_path):
    """The honest half: the same fixture, one mutation reversed, is green."""
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh", _gate_stub("shell-patterns", rc=1))
    rep, (rc, lines) = run(tree)
    # rc 1 is a VERDICT (the gate assessed and found something), never a blind gate
    assert rc == E.OK, "\n".join(lines)
    assert verdicts(rep)["pattern"] == E.ENFORCED


# -- the known-gaps ratchet --------------------------------------------------

def test_a_listed_gap_is_honoured_and_reported(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh", _gate_stub("shell-patterns", rc=2))
    _write(tree / E.KNOWN_GAPS_FILE, json.dumps({
        "schema": "ao.futureproof.known-gaps/v1",
        "entries": [{"mechanism": "pattern", "link": E.LINK_ASSESSES,
                     "gate": "shell-patterns", "issue": 9999, "reason": "test"}],
    }))
    rep, (rc, lines) = run(tree)
    text = "\n".join(lines)
    assert rc == E.OK, text
    assert "KNOWN" in text and "#9999" in text
    assert verdicts(rep)["pattern"] == E.DECLARED_ONLY


def test_a_stale_exemption_fails(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / E.KNOWN_GAPS_FILE, json.dumps({
        "schema": "ao.futureproof.known-gaps/v1",
        "entries": [{"mechanism": "pattern", "link": E.LINK_ASSESSES,
                     "gate": "shell-patterns", "issue": 9999, "reason": "stale"}],
    }))
    rep, (rc, lines) = run(tree)
    text = "\n".join(lines)
    assert rc == E.NOT_OK
    assert "known-gap-stale" in text


def test_a_missing_gap_file_is_not_a_bypass(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / "scripts" / "check-shell-patterns.sh", _gate_stub("shell-patterns", rc=2))
    assert not (tree / E.KNOWN_GAPS_FILE).exists()
    rep, (rc, _) = run(tree)
    assert rc == E.NOT_OK  # fail-closed: no file means no exemptions


def test_an_invalid_gap_manifests_is_cannot_assess(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / E.KNOWN_GAPS_FILE, "{ not json")
    with pytest.raises(E.CannotAssess):
        E.report(tree, E.MECHANISMS, timeout=60)


def test_an_exemption_naming_an_unknown_mechanism_fails(tmp_path):
    tree = build_tree(tmp_path)
    _write(tree / E.KNOWN_GAPS_FILE, json.dumps({
        "schema": "ao.futureproof.known-gaps/v1",
        "entries": [{"mechanism": "nope", "link": E.LINK_ASSESSES,
                     "gate": "shell-patterns", "issue": 9999, "reason": "bogus"}],
    }))
    rep, (rc, lines) = run(tree)
    assert rc == E.NOT_OK
    assert "known-gap-invalid" in "\n".join(lines)


# -- the repository-wide halves ---------------------------------------------

def test_the_mechanism_set_is_complete_and_named(tmp_path):
    assert E.completeness_problem(E.MECHANISMS) is None
    dropped = [m for m in E.MECHANISMS if m["id"] != "rca"]
    assert "rca" in (E.completeness_problem(dropped) or "")
    renamed = [dict(m, id="extra") if m["id"] == "rca" else m for m in E.MECHANISMS]
    assert "extra" in (E.completeness_problem(renamed) or "")


def test_the_mechanisms_are_disjoint(tmp_path):
    assert E.disjointness_problem(E.MECHANISMS) is None
    shared = [dict(m) for m in E.MECHANISMS[:2]]
    shared[1] = dict(shared[1], gates=shared[0]["gates"])
    assert "two mechanisms" in (E.disjointness_problem(shared) or "")


# -- CANNOT-ASSESS, never a pass --------------------------------------------

def test_a_missing_root_is_cannot_assess(tmp_path, capsys):
    rc = E.main(["--root", str(tmp_path / "absent")])
    assert rc == E.CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_an_unknown_mechanism_is_cannot_assess(tmp_path, capsys):
    tree = build_tree(tmp_path)
    rc = E.main(["--root", str(tree), "--mechanism", "nope"])
    assert rc == E.CANNOT_ASSESS
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_a_tree_without_the_discovery_layer_is_cannot_assess(tmp_path):
    tree = build_tree(tmp_path)
    (tree / "scripts" / "discover-checks.sh").unlink()
    with pytest.raises(E.CannotAssess):
        E.report(tree, E.MECHANISMS, timeout=60)


# -- the module's own self-description --------------------------------------

def test_the_list_flag_prints_the_table(tmp_path, capsys):
    assert E.main(["--list"]) == E.OK
    out = capsys.readouterr().out
    for mech in E.EXPECTED_MECHANISMS:
        assert mech in out


def test_the_json_report_carries_every_mechanism(tmp_path, capsys):
    tree = build_tree(tmp_path)
    assert E.main(["--root", str(tree), "--json"]) == E.OK
    doc = json.loads(capsys.readouterr().out)
    assert {row["mechanism"] for row in doc["mechanisms"]} == set(E.EXPECTED_MECHANISMS)
    assert all(row["probed"] for row in doc["mechanisms"])
