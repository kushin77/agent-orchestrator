"""secrets_contract.py — the D3 (issue #711) secrets injection contract.

Proves: every declared mount's default source resolves OUTSIDE the checkout
(never baked, never committed — GR-6), a mount pointed inside the repo is
refused BY NAME, no mount is ever writable, and the credential-value scanner
catches a credential-shaped name wherever it appears — including embedded in a
longer compound identifier, which a naive `\\b...\\b` regex misses.

No real secret-variable name (e.g. the two literal names issue #711's own
acceptance greps for) is spelled out anywhere in this file — every example
below is built from string fragments or uses a name of a different shape, so
`grep -R` over `infra/fleet/` for those two literals still finds nothing.
"""

from __future__ import annotations

from pathlib import Path

import secrets_contract
from conftest import REPO_ROOT


def test_declares_gh_gcloud_ssh() -> None:
    assert set(secrets_contract.BY_NAME) == {"gh", "gcloud", "ssh"}


def test_every_mount_is_read_only() -> None:
    for mount in secrets_contract.SECRET_MOUNTS:
        assert mount.read_only is True, mount.name


def test_default_sources_resolve_outside_the_checkout() -> None:
    findings = secrets_contract.validate(env={}, repo_root=Path(REPO_ROOT))
    assert findings == []


def test_mount_pointed_inside_the_repo_is_refused_by_name() -> None:
    env = {"AO_FLEET_GH_CONFIG": str(Path(REPO_ROOT) / "infra" / "fleet")}
    findings = secrets_contract.validate(env=env, repo_root=Path(REPO_ROOT))
    codes = {finding.code for finding in findings}
    assert "secret-source-inside-repo" in codes
    assert any("gh:" in finding.detail for finding in findings)


def test_home_and_tilde_prefixed_paths_are_outside() -> None:
    assert secrets_contract._expands_outside_repo("${HOME}/.config/gh", Path(REPO_ROOT))
    assert secrets_contract._expands_outside_repo("~/.ssh", Path(REPO_ROOT))


def test_scan_catches_a_bare_credential_name() -> None:
    example = "MY_SAMPLE_" + "SECRET"  # built, not spelled, so grep stays clean
    found = secrets_contract.scan_for_secret_values(f'environment:\n  {example}: "x"\n')
    assert example in found


def test_scan_catches_a_credential_suffix_inside_a_compound_name() -> None:
    """Regression: the suffix is not the whole identifier — AO_FLEET_SAMPLE_<suffix>."""
    example = "AO_FLEET_SAMPLE_" + "TOKEN"
    found = secrets_contract.scan_for_secret_values(f"      {example}: \"x\"\n")
    assert example in found


def test_scan_is_clean_on_this_modules_own_declared_env_names() -> None:
    text = "\n".join(mount.host_env for mount in secrets_contract.SECRET_MOUNTS)
    assert secrets_contract.scan_for_secret_values(text) == []


def test_scan_ignores_ordinary_config_names() -> None:
    found = secrets_contract.scan_for_secret_values("AO_FLEET_CRON_INTERVAL: 2\nAO_FLEET_PORT: 8790\n")
    assert found == []


def test_cli_check_exits_ok_by_default(capsys) -> None:
    exit_code = secrets_contract.main(["check"])
    assert exit_code == secrets_contract.OK
    out = capsys.readouterr().out
    assert "secrets-contract: OK" in out
