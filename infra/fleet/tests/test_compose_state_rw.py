"""docker-compose.agent-cron.yml — the D3 (issue #711) state-rw + secrets posture.

Proves, from the compose file itself (parsed, not read as prose): the primary
`agent-cron` service is unchanged (no profile, both state roots read-only —
dev-run stays read-only BY DEFAULT), the new `agent-cron-rw` service is gated
behind a `profiles:` key so `docker compose up` never starts it, and its
writable state mounts + secrets binds are explicit (never inherited via a
compose merge key, which does not deep-merge).
"""

from __future__ import annotations

import os

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Computed locally, not `from conftest import REPO_ROOT`: a bare `conftest`
# import name collides with `fleet/tests/conftest.py` when both suites are
# collected in the same `pytest` invocation (issue #710/#1108's
# `python3 -m pytest -q infra/fleet/tests fleet/tests`) — whichever
# `conftest.py` pytest resolved first for the process wins the module-cache
# slot named `conftest`, so the other suite's bare import either fails or
# silently binds the wrong module. `infra/fleet/tests/conftest.py` still runs
# on its own (it inserts `infra/fleet` onto `sys.path`); this file just no
# longer reaches back into it by that ambiguous name.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

COMPOSE_PATH = os.path.join(REPO_ROOT, "infra", "fleet", "docker-compose.agent-cron.yml")

pytestmark = pytest.mark.skipif(yaml is None, reason="PyYAML is not installed")


@pytest.fixture(scope="module")
def compose() -> dict:
    with open(COMPOSE_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _volume(service: dict, target: str) -> dict:
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict) and volume.get("target") == target:
            return volume
    raise AssertionError(f"no volume targets {target!r}")


def test_primary_service_has_no_profile(compose: dict) -> None:
    primary = compose["services"]["agent-cron"]
    assert not primary.get("profiles")


def test_primary_service_state_mounts_stay_read_only(compose: dict) -> None:
    primary = compose["services"]["agent-cron"]
    for target in ("/repo/.fleet", "/repo/.board"):
        volume = _volume(primary, target)
        assert volume["read_only"] is True
        assert volume["bind"]["create_host_path"] is False


def test_rw_service_is_gated_behind_a_profile(compose: dict) -> None:
    rw = compose["services"]["agent-cron-rw"]
    assert rw.get("profiles") == ["state-rw"]


def test_rw_service_state_mounts_are_writable(compose: dict) -> None:
    rw = compose["services"]["agent-cron-rw"]
    for target in ("/repo/.fleet", "/repo/.board"):
        volume = _volume(rw, target)
        assert volume["read_only"] is False
        assert volume["bind"]["create_host_path"] is False


def test_rw_service_declares_the_four_acceptance_binds(compose: dict) -> None:
    """Issue #711's own acceptance: <repo>:/repo, ./.fleet, ./.board, fleet-logs."""
    rw = compose["services"]["agent-cron-rw"]
    targets = {volume.get("target") for volume in rw.get("volumes") or [] if isinstance(volume, dict)}
    assert {"/repo", "/repo/.fleet", "/repo/.board", "/var/log/fleet"} <= targets


def test_repo_bind_is_read_only(compose: dict) -> None:
    rw = compose["services"]["agent-cron-rw"]
    repo_volume = _volume(rw, "/repo")
    assert repo_volume["read_only"] is True


def test_fleet_logs_is_a_named_volume_declared_at_top_level(compose: dict) -> None:
    assert "fleet-logs" in (compose.get("volumes") or {})


def test_secrets_are_read_only_and_never_an_env_file(compose: dict) -> None:
    rw = compose["services"]["agent-cron-rw"]
    assert "env_file" not in rw
    for target in ("/root/.config/gh", "/root/.config/gcloud", "/root/.ssh"):
        volume = _volume(rw, target)
        assert volume["read_only"] is True
        assert volume["type"] == "bind"


def test_secrets_sources_are_outside_the_repo(compose: dict) -> None:
    rw = compose["services"]["agent-cron-rw"]
    for target in ("/root/.config/gh", "/root/.config/gcloud", "/root/.ssh"):
        source = _volume(rw, target)["source"]
        assert "${HOME}" in source or source.startswith("~")


def test_state_rw_flag_declared_in_environment(compose: dict) -> None:
    rw = compose["services"]["agent-cron-rw"]
    assert rw["environment"]["AO_FLEET_STATE_RW"] == "1"
    primary = compose["services"]["agent-cron"]
    assert "AO_FLEET_STATE_RW" not in (primary.get("environment") or {})


def test_dry_run_stays_pinned_on_the_rw_service(compose: dict) -> None:
    """Writable mounts are not permission to apply (D2's rule, unedited)."""
    rw = compose["services"]["agent-cron-rw"]
    assert rw["environment"]["AO_FLEET_DRY_RUN"] == "1"


def test_docker_socket_mounted_on_rw_service_only(compose: dict) -> None:
    """The promote-portal rung's Docker access (#1329/#1341), least-privilege:

    only `agent-cron-rw` (flag-gated, credentialed) gets a path to the host's
    Docker daemon; the dry-run-only `agent-cron` sibling never does.
    """
    rw = compose["services"]["agent-cron-rw"]
    socket = _volume(rw, "/var/run/docker.sock")
    assert socket["source"] == "/var/run/docker.sock"
    assert socket["read_only"] is True
    assert socket["bind"]["create_host_path"] is False

    primary = compose["services"]["agent-cron"]
    targets = {volume.get("target") for volume in primary.get("volumes") or [] if isinstance(volume, dict)}
    assert "/var/run/docker.sock" not in targets


def test_no_denylisted_credential_name_anywhere_in_infra_fleet() -> None:
    """Mirrors issue #711's own acceptance grep, run as a test rather than only CI evidence."""
    denylist = ("GH_" + "TOKEN", "DEEPSEEK_API_" + "KEY")
    fleet_dir = os.path.join(REPO_ROOT, "infra", "fleet")
    hits = []
    for dirpath, dirnames, filenames in os.walk(fleet_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if filename.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
            except OSError:
                continue
            for name in denylist:
                if name in text:
                    hits.append((path, name))
    assert hits == []
