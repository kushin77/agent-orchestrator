"""Tests for governance/knowledge/live_sync.py (issue #887)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import live_sync


REAL_PIN = live_sync.REPO_ROOT / "cmr-pin.yaml"


def _require_vendor_cmr():
    if not live_sync.VALIDATOR_PATH.is_file():
        pytest.skip("vendor/CMR submodule not checked out (validator missing)")


def test_live_vendor_head_matches_git():
    _require_vendor_cmr()
    import subprocess

    expected = subprocess.run(
        ["git", "-C", str(live_sync.VENDOR_CMR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert live_sync.live_vendor_head() == expected


def test_pinned_bundle_no_drift_on_real_pin():
    _require_vendor_cmr()
    if not REAL_PIN.is_file():
        pytest.skip("no cmr-pin.yaml at repo root")
    bundle = live_sync.pinned_bundle(REAL_PIN)
    assert bundle.drift is False
    assert bundle.bundle_ref == bundle.live_head


def test_check_drift_returns_none_when_clean():
    _require_vendor_cmr()
    if not REAL_PIN.is_file():
        pytest.skip("no cmr-pin.yaml at repo root")
    assert live_sync.check_drift(REAL_PIN) is None


def test_pinned_bundle_refuses_drift_by_name(tmp_path: Path):
    """Negative control: a mutated bundle_ref is refused as CMR_PIN_DRIFT."""
    _require_vendor_cmr()
    if not REAL_PIN.is_file():
        pytest.skip("no cmr-pin.yaml at repo root")

    with REAL_PIN.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    live_head = live_sync.live_vendor_head()
    mutant_ref = "0" * 40
    assert mutant_ref != live_head

    data["bundle_ref"] = mutant_ref
    mutant_path = tmp_path / "cmr-pin.yaml"
    with mutant_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)

    with pytest.raises(live_sync.PinSyncError) as excinfo:
        live_sync.pinned_bundle(mutant_path, require_no_drift=True)
    assert excinfo.value.code == live_sync.CODE_PIN_DRIFT

    # And the non-raising reporter path names it too.
    err = live_sync.check_drift(mutant_path)
    assert err is not None
    assert err.code == live_sync.CODE_PIN_DRIFT


def test_pinned_bundle_without_require_no_drift_reports_instead_of_raising(tmp_path: Path):
    _require_vendor_cmr()
    if not REAL_PIN.is_file():
        pytest.skip("no cmr-pin.yaml at repo root")

    with REAL_PIN.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["bundle_ref"] = "0" * 40
    mutant_path = tmp_path / "cmr-pin.yaml"
    with mutant_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)

    bundle = live_sync.pinned_bundle(mutant_path, require_no_drift=False)
    assert bundle.drift is True


def test_pin_missing_raises_named_error(tmp_path: Path):
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(live_sync.PinSyncError) as excinfo:
        live_sync.validate_pin_shape(missing)
    assert excinfo.value.code == live_sync.CODE_PIN_MISSING


def test_malformed_yaml_raises_schema_invalid(tmp_path: Path):
    _require_vendor_cmr()
    bad = tmp_path / "cmr-pin.yaml"
    bad.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(live_sync.PinSyncError) as excinfo:
        live_sync._load_yaml(bad)
    assert excinfo.value.code == live_sync.CODE_SCHEMA_INVALID
