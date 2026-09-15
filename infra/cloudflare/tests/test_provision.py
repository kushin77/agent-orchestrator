"""Tests for the pure provision + connector path (issue #785).

The route publishes one hostname through a remotely-managed Cloudflare Tunnel.
Before it can publish, the tunnel must exist and a `cloudflared` connector must
run. What has to hold for the provision half is therefore mostly about what it
must NOT do:

  * find-or-create must be idempotent -- an existing tunnel is reused, never
    created a second time, and only a genuinely empty list authorises a create;
  * the read must be fail-closed -- an unreadable list is a refusal, never
    mistaken for "no tunnel exists";
  * the connector deploy must converge (idempotent) and never embed a token.

The same properties are provoked against a *neutered* copy of this module by
`scripts/check-ao-ssh-access.sh`, so a test that stopped catching the fail-open
read would fail the gate of record rather than pass quietly.
"""

from __future__ import annotations

import pytest

from infra.cloudflare.provision import (
    CATCHALL_SERVICE,
    connector_container_name,
    connector_deploy_lines,
    initial_tunnel_config,
    tunnel_id_from_list,
)

TUNNEL_ID = "stub-tunnel-id-1234"


def tunnel_list(*entries: dict) -> dict:
    return {"success": True, "errors": [], "result": list(entries)}


# --- find-or-create is idempotent ------------------------------------------


def test_an_existing_tunnel_is_reused_by_id() -> None:
    document = tunnel_list({"id": TUNNEL_ID, "name": "stub-tunnel"})

    assert tunnel_id_from_list(document) == TUNNEL_ID


def test_a_genuinely_empty_list_authorises_a_create() -> None:
    assert tunnel_id_from_list(tunnel_list()) == ""


def test_only_the_first_tunnel_is_returned() -> None:
    document = tunnel_list(
        {"id": "first", "name": "a"}, {"id": "second", "name": "b"}
    )

    assert tunnel_id_from_list(document) == "first"


# --- the read is fail-closed --------------------------------------------------


@pytest.mark.parametrize("bad", [None, [], "nope", 3])
def test_a_non_object_response_is_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        tunnel_id_from_list(bad)


def test_a_non_success_response_is_refused() -> None:
    with pytest.raises(ValueError):
        tunnel_id_from_list({"success": False, "errors": [{"message": "denied"}]})


def test_a_response_without_a_result_array_is_refused() -> None:
    with pytest.raises(ValueError):
        tunnel_id_from_list({"success": True, "result": None})


def test_a_list_entry_without_an_id_is_refused() -> None:
    with pytest.raises(ValueError):
        tunnel_id_from_list({"success": True, "result": [{"name": "no-id"}]})


def test_a_non_object_list_entry_is_refused() -> None:
    with pytest.raises(ValueError):
        tunnel_id_from_list({"success": True, "result": ["not-an-object"]})


# --- the seed config ----------------------------------------------------------


def test_a_new_tunnel_is_seeded_with_exactly_one_catch_all() -> None:
    config = initial_tunnel_config()

    assert config == {"config": {"ingress": [{"service": CATCHALL_SERVICE}]}}
    assert config["config"]["ingress"][0]["service"] == "http_status:404"


# --- the connector deploy ------------------------------------------------------


def test_the_container_name_normalises_dots_and_commas() -> None:
    assert connector_container_name("ao-tunnel", ["ssh.example.test"]) == (
        "ao-tunnel-ssh-example-test"
    )
    assert connector_container_name("ao-tunnel", ["a,b.example"]) == (
        "ao-tunnel-a-b-example"
    )


def test_the_container_name_joins_multiple_hostnames() -> None:
    assert connector_container_name("ao-tunnel", ["a.test", "b.test"]) == (
        "ao-tunnel-a-test-b-test"
    )


@pytest.mark.parametrize("prefix", ["", "   "])
def test_a_blank_container_prefix_is_refused(prefix: str) -> None:
    with pytest.raises(ValueError):
        connector_container_name(prefix, ["ssh.example.test"])


def test_the_deploy_is_idempotent_and_token_free() -> None:
    lines = connector_deploy_lines("ao-tunnel", "cloudflare/cloudflared:2026.7.2")

    assert lines[0] == "docker pull cloudflare/cloudflared:2026.7.2"
    # `docker rm -f ... || true` before `docker run` is what makes a re-run
    # converge instead of colliding on the existing container name.
    assert any("docker rm -f" in line and "|| true" in line for line in lines)
    run = next(line for line in lines if line.startswith("docker run"))
    assert "--restart unless-stopped" in run
    assert "--name ao-tunnel" in run
    # The token arrives via docker's inherit-from-environment form: no literal
    # token, and no `TUNNEL_TOKEN=<value>` with a value, can appear here.
    assert "-e TUNNEL_TOKEN" in run
    assert "TUNNEL_TOKEN=" not in run


def test_the_deploy_uses_the_requested_network() -> None:
    lines = connector_deploy_lines("ao-tunnel", "img", network="shared-services-net")

    run = next(line for line in lines if line.startswith("docker run"))
    assert "--network shared-services-net" in run


@pytest.mark.parametrize(("container", "image"), [("", "img"), ("c", "")])
def test_a_blank_deploy_argument_is_refused(container: str, image: str) -> None:
    with pytest.raises(ValueError):
        connector_deploy_lines(container, image)
