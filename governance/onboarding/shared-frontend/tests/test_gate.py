"""Gate contract: the bash gate's tri-state, measured on scratch trees.

This is the gate's own self-control (issue #703): the gate is run by path with
`--root <scratch>`, so its 0 / 1 / 2 contract is asserted without touching the
checkout under test. The gate's *internal* self-proof (which also exercises the
0/1/2 states) is asserted by the last test.
"""

import os
import subprocess
from pathlib import Path

import yaml

LANE_REL = Path("governance/onboarding/shared-frontend")
GATE = Path(__file__).resolve().parents[4] / "scripts/check-shared-frontend-onboarding.sh"


def run_gate(*args):
    env = dict(os.environ)
    # The gate skips its nested renderer suite when it runs under pytest; without
    # the marker a gate run from a test would recurse into this very suite.
    env.setdefault("PYTEST_CURRENT_TEST", "test_gate")
    return subprocess.run(
        ["bash", str(GATE), *args], capture_output=True, text=True, env=env
    )


def output_of(proc):
    return proc.stdout + proc.stderr


def test_gate_is_ok_on_a_clean_tree(clean_repo):
    proc = run_gate("--root", str(clean_repo))
    assert proc.returncode == 0, output_of(proc)
    assert "shared-frontend-onboarding: OK" in output_of(proc)


def test_gate_is_not_ok_when_tokens_is_missing(clean_repo):
    (clean_repo / "tokens.json").unlink()
    proc = run_gate("--root", str(clean_repo))
    assert proc.returncode == 1, output_of(proc)
    assert "tokens.json: MISSING" in output_of(proc)


def test_gate_is_not_ok_when_the_manifest_is_missing(clean_repo):
    (clean_repo / "gdc-manifest.yaml").unlink()
    proc = run_gate("--root", str(clean_repo))
    assert proc.returncode == 1, output_of(proc)
    assert "gdc-manifest.yaml: MISSING" in output_of(proc)


def test_gate_is_not_ok_on_an_unknown_tenant(clean_repo):
    lane = clean_repo / LANE_REL
    doc = yaml.safe_load((lane / "instance.yaml").read_text(encoding="utf-8"))
    doc["tenant"] = "kushin77-typo"
    (lane / "instance.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8"
    )
    proc = run_gate("--root", str(clean_repo))
    assert proc.returncode == 1, output_of(proc)
    assert "unknown tenant 'kushin77-typo'" in output_of(proc)


def test_gate_is_not_ok_when_an_asset_is_not_the_render(clean_repo):
    manifest = clean_repo / "gdc-manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "# hand edit\n", encoding="utf-8"
    )
    proc = run_gate("--root", str(clean_repo))
    assert proc.returncode == 1, output_of(proc)
    assert "not the render of the instance" in output_of(proc)


def test_gate_cannot_assess_an_unparseable_lane(clean_repo):
    (clean_repo / LANE_REL / "instance.yaml").write_text("org: [unclosed\n", encoding="utf-8")
    proc = run_gate("--root", str(clean_repo))
    assert proc.returncode == 2, output_of(proc)
    assert "CANNOT-ASSESS" in output_of(proc)


def test_gate_cannot_assess_a_missing_renderer(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run_gate("--root", str(empty))
    assert proc.returncode == 2, output_of(proc)
    assert "CANNOT-ASSESS" in output_of(proc)


def test_gate_refuses_an_unknown_argument():
    proc = run_gate("--nope")
    assert proc.returncode == 2, output_of(proc)
    assert "unknown argument" in output_of(proc)


def test_gate_self_proof_passes_on_the_real_tree():
    """The gate's own provoked controls run on the real tree and must be green."""
    proc = run_gate()
    assert proc.returncode == 0, output_of(proc)
    text = output_of(proc)
    assert "self-proof passed" in text
    assert "deleting tokens.json is refused" in text
    assert "a token set that drifted from the pinned rev is refused" in text
    assert "dropping the shared-frontend.tokens pin is refused" in text
    assert "an unknown tenant is refused by name" in text
    assert "an unparseable lane input is CANNOT-ASSESS" in text
