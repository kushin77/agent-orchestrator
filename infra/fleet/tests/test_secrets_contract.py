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

import os
from pathlib import Path

import secrets_contract

# Computed locally, not `from conftest import REPO_ROOT`: a bare `conftest`
# import name collides with `fleet/tests/conftest.py` when both suites are
# collected in the same `pytest` invocation (issue #710/#1108's
# `python3 -m pytest -q infra/fleet/tests fleet/tests`) — whichever
# `conftest.py` pytest resolved first for the process wins the module-cache
# slot named `conftest`, so the other suite's bare import either fails or
# silently binds the wrong module. `infra/fleet/tests/conftest.py` still runs
# (it inserts `infra/fleet` onto `sys.path` for `import secrets_contract`
# above); this file just no longer reaches back into it by that ambiguous name.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_declares_gh_gcloud_ssh() -> None:
    # issue #1329 adds a fourth mount (ar-reader-key) for promote_portal.py's
    # Artifact Registry read auth — same declaration shape as the other three.
    # issue #1784 adds a fifth (deepseek): the fleet's MODEL credential, which
    # was the one credential required as ad-hoc environment variables and the
    # only one absent from this declaration.
    assert set(secrets_contract.BY_NAME) == {
        "gh",
        "gcloud",
        "ssh",
        "ar-reader-key",
        "deepseek",
    }


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


def test_the_model_credential_is_declared_like_every_other() -> None:
    """#1784: the credential the fleet cannot start without is declared too.

    The runner refuses to dispatch until its model credential is present (#841).
    That credential is provisioned by this declaration rather than exported by an
    operator, so it must satisfy the same two properties as the other four:
    sourced outside the checkout, and mounted read-only.
    """
    mount = secrets_contract.BY_NAME["deepseek"]
    assert mount.read_only is True
    assert secrets_contract._expands_outside_repo(
        mount.default_host_path, Path(REPO_ROOT)
    )


def test_a_model_credential_pointed_inside_the_repo_is_refused_by_name() -> None:
    """The new mount is load-bearing, not decorative.

    Same provocation as the `gh` one above, aimed at the mount this change adds:
    if the model credential's source could resolve inside the checkout, the
    declaration would be defending nothing.
    """
    env = {"AO_FLEET_DEEPSEEK_CONFIG": str(Path(REPO_ROOT) / "infra" / "fleet")}
    findings = secrets_contract.validate(env=env, repo_root=Path(REPO_ROOT))
    codes = {finding.code for finding in findings}
    assert "secret-source-inside-repo" in codes
    assert any("deepseek:" in finding.detail for finding in findings)


def test_the_module_source_spells_no_credential_variable_name() -> None:
    """GR-6, made mechanical (issue #1784).

    The module's own head states the rule — "No literal credential-variable name
    is spelled out in this file (or its tests)" — but nothing checked it, so a
    declaration added later could name a credential and still pass every arm.
    That is how this test came to exist: the `deepseek` mount's `why` prose
    spelled two such names on its first draft, and only a hand-run of the
    scanner caught it.

    The exception is the single example the regex comment uses to explain
    itself. It is named here rather than exempted by pattern, so the rule cannot
    widen silently: any NEW credential-shaped name fails this arm.
    """
    source = (
        Path(REPO_ROOT) / "infra" / "fleet" / "secrets_contract.py"
    ).read_text(encoding="utf-8")
    assert secrets_contract.scan_for_secret_values(source) == [
        "AO_FLEET_SAMPLE_TOKEN"
    ], "a credential-variable name is spelled out in the module source"


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
