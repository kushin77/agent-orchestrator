"""CLI end-to-end smoke tests (issue #42): exit codes are real, no false green.

Exercises the ``aoi`` command line the way a consumer/operator would: render
--check (byte-stable), conformance, drift, and override validation — with
genuine pass and fail paths.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_INSTR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXAMPLE = os.path.join(_INSTR, "example")
_RENDERED = os.path.join(_EXAMPLE, "rendered")
_CANONICAL = os.path.join(_EXAMPLE, "canonical.yaml")
_OVERRIDE = os.path.join(_EXAMPLE, "tenant-override.json")
_MANIFEST = os.path.join(_RENDERED, "distribution-manifest.json")


def _run(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = _INSTR + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "aoi", *args],
        cwd=_INSTR,
        env=env,
        capture_output=True,
        text=True,
    )


def test_cli_render_check_is_byte_stable():
    result = _run("render", "--canonical", _CANONICAL, "--override", _OVERRIDE, "--out", _RENDERED, "--check")
    assert result.returncode == 0, result.stderr
    assert "byte-stable" in result.stdout


def test_cli_conformance_passes_on_committed_example():
    result = _run("conformance", "--dir", _RENDERED, "--canonical", _CANONICAL, "--override", _OVERRIDE)
    assert result.returncode == 0, result.stderr
    assert "conformance: PASS" in result.stdout


def test_cli_drift_flags_an_old_version_consumer(tmp_path):
    manifest = json.load(open(_MANIFEST, encoding="utf-8"))
    old = {
        "schema": "ao.instructions.consumer/v1",
        "consumer": "acme/old",
        "canonical": {"id": manifest["canonical"]["id"], "version": "0.1.0"},
        "mirrors": manifest["mirrors"],
    }
    consumer_path = os.path.join(tmp_path, "old-consumer.json")
    with open(consumer_path, "w", encoding="utf-8") as handle:
        json.dump(old, handle, indent=2)
    result = _run("drift", "--manifest", _MANIFEST, "--consumer", consumer_path)
    assert result.returncode == 1, result.stdout
    assert "behind" in result.stderr


def test_cli_drift_passes_for_a_current_consumer(tmp_path):
    manifest = json.load(open(_MANIFEST, encoding="utf-8"))
    current = {
        "schema": "ao.instructions.consumer/v1",
        "consumer": "acme/current",
        "canonical": manifest["canonical"],
        "mirrors": manifest["mirrors"],
    }
    consumer_path = os.path.join(tmp_path, "current-consumer.json")
    with open(consumer_path, "w", encoding="utf-8") as handle:
        json.dump(current, handle, indent=2)
    result = _run("drift", "--manifest", _MANIFEST, "--consumer", consumer_path)
    assert result.returncode == 0, result.stderr
    assert "compliant" in result.stdout


def test_cli_validate_override_accepts_the_valid_example():
    result = _run("validate-override", "--override", _OVERRIDE, "--canonical", _CANONICAL)
    assert result.returncode == 0, result.stderr
    assert "override: valid" in result.stdout


def test_cli_validate_override_rejects_out_of_contract_override(tmp_path):
    override = json.load(open(_OVERRIDE, encoding="utf-8"))
    override["modifyGovernedRules"] = True  # outside the frozen contract
    bad_path = os.path.join(tmp_path, "bad-override.json")
    with open(bad_path, "w", encoding="utf-8") as handle:
        json.dump(override, handle, indent=2)
    result = _run("validate-override", "--override", bad_path)
    assert result.returncode == 1
    assert "REJECTED" in result.stderr
