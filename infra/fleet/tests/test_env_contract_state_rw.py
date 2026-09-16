"""AO_FLEET_STATE_RW — the D3 (issue #711) flag in env_contract.py.

Proves the flag defaults OFF ("0") and that only "0"/"1" are accepted, exactly
like env_contract.py's other one-of flags (AO_FLEET_DRY_RUN, AO_FLEET_CRON_NO_INSTALL).
"""

from __future__ import annotations

import env_contract


def test_state_rw_declared() -> None:
    assert "AO_FLEET_STATE_RW" in env_contract.BY_NAME


def test_state_rw_defaults_off() -> None:
    var = env_contract.BY_NAME["AO_FLEET_STATE_RW"]
    assert var.default == "0"
    resolved = env_contract.resolve(env={})
    assert resolved["AO_FLEET_STATE_RW"] == "0"


def test_state_rw_accepts_only_0_or_1() -> None:
    var = env_contract.BY_NAME["AO_FLEET_STATE_RW"]
    assert var.invalid("0") is None
    assert var.invalid("1") is None
    assert var.invalid("2") is not None
    assert var.invalid("true") is not None


def test_state_rw_invalid_value_is_a_refusal() -> None:
    findings = env_contract.validate(env={"AO_FLEET_STATE_RW": "yes"})
    codes = {finding.code for finding in findings}
    assert "state-rw-not-a-flag" in codes


def test_dry_run_still_required_regardless_of_state_rw() -> None:
    """A writable state mount is never permission to apply (D2's rule, unedited)."""
    findings = env_contract.validate(env={"AO_FLEET_STATE_RW": "1", "AO_FLEET_DRY_RUN": "0"})
    codes = {finding.code for finding in findings}
    assert "dry-run-required" in codes
