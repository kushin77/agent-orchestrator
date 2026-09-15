"""Tests for the pure ingress merge (issue #771).

The route publishes one hostname through a remotely-managed Cloudflare Tunnel
by rewriting the tunnel's whole `config.ingress` array. What has to hold is
therefore mostly about what the merge must NOT do:

  * an unrelated rule the tunnel already serves must survive, in place;
  * a rule for the same hostname must be updated, never duplicated;
  * a new rule must land immediately before the trailing catch-all, so the
    catch-all stays last;
  * the caller's list must not be touched at all (the function is pure).

The same properties are provoked against a *neutered* copy of this module by
`scripts/check-ao-ssh-access.sh`, so a test that stopped catching the blind
replacement would fail the gate of record rather than pass quietly.
"""

from __future__ import annotations

import pytest

from infra.cloudflare.ingress import (
    DEFAULT_SSH_PORT,
    merge_ssh_rule,
    ssh_service,
    tunnel_cname,
    tunnel_config_ingress,
)

UNRELATED = {"hostname": "gitlab.example.test", "service": "http://gitlab:80"}
OTHER = {"hostname": "auth.example.test", "service": "https://auth.internal:443"}
CATCH_ALL = {"service": "http_status:404"}
HOST = "ssh.example.test"
SERVICE = "ssh://192.0.2.10:22"


def ssh_rule() -> dict:
    return {"hostname": HOST, "service": SERVICE}


# --- the unrelated rule survives -------------------------------------------


def test_an_unrelated_rule_survives_untouched_and_in_place() -> None:
    merged = merge_ssh_rule([dict(UNRELATED), dict(OTHER), dict(CATCH_ALL)], HOST, SERVICE)

    assert merged[0] == UNRELATED
    assert merged[1] == OTHER
    assert len(merged) == 4


def test_the_catch_all_stays_last_and_unchanged() -> None:
    merged = merge_ssh_rule([dict(UNRELATED), dict(CATCH_ALL)], HOST, SERVICE)

    assert merged[-1] == CATCH_ALL


def test_the_input_is_never_mutated() -> None:
    original = [dict(UNRELATED), dict(OTHER), dict(CATCH_ALL)]
    before = [dict(rule) for rule in original]

    merge_ssh_rule(original, HOST, SERVICE)

    assert original == before


def test_the_returned_rules_are_copies_not_the_caller_s_objects() -> None:
    original = [dict(UNRELATED), dict(CATCH_ALL)]

    merged = merge_ssh_rule(original, HOST, SERVICE)

    assert merged[0] is not original[0]
    merged[0]["service"] = "http://changed:1"
    assert original[0]["service"] == UNRELATED["service"]


# --- the same hostname is updated, not duplicated --------------------------


def test_a_rule_for_the_same_hostname_is_updated_in_place() -> None:
    stale = {"hostname": HOST, "service": "http://stale-host:80"}
    merged = merge_ssh_rule([dict(UNRELATED), stale, dict(CATCH_ALL)], HOST, SERVICE)

    assert len(merged) == 3
    assert merged[1] == ssh_rule()
    assert [rule for rule in merged if rule.get("hostname") == HOST] == [ssh_rule()]


def test_a_preexisting_rule_for_the_hostname_keeps_its_other_options() -> None:
    configured = {
        "hostname": HOST,
        "service": "http://stale-host:80",
        "originRequest": {"connectTimeout": 30},
    }

    merged = merge_ssh_rule([configured, dict(CATCH_ALL)], HOST, SERVICE)

    assert merged[0]["service"] == SERVICE
    assert merged[0]["originRequest"] == {"connectTimeout": 30}
    assert merged[0]["hostname"] == HOST


def test_merging_twice_changes_nothing_the_second_time() -> None:
    once = merge_ssh_rule([dict(UNRELATED), dict(CATCH_ALL)], HOST, SERVICE)

    twice = merge_ssh_rule(once, HOST, SERVICE)

    assert twice == once


# --- where the new rule lands ----------------------------------------------


def test_a_new_rule_lands_immediately_before_the_catch_all() -> None:
    merged = merge_ssh_rule([dict(UNRELATED), dict(OTHER), dict(CATCH_ALL)], HOST, SERVICE)

    assert merged[-2] == ssh_rule()
    assert merged[-1] == CATCH_ALL
    assert merged.index(ssh_rule()) > merged.index(UNRELATED)


def test_an_ingress_without_a_catch_all_gets_the_rule_appended() -> None:
    merged = merge_ssh_rule([dict(UNRELATED), dict(OTHER)], HOST, SERVICE)

    assert merged == [UNRELATED, OTHER, ssh_rule()]


def test_a_catch_all_that_is_not_last_does_not_displace_a_hostname_rule() -> None:
    misplaced = [dict(CATCH_ALL), dict(UNRELATED)]

    merged = merge_ssh_rule(misplaced, HOST, SERVICE)

    assert merged == [CATCH_ALL, UNRELATED, ssh_rule()]


def test_an_empty_ingress_yields_just_the_rule() -> None:
    assert merge_ssh_rule([], HOST, SERVICE) == [ssh_rule()]


# --- the refusals ----------------------------------------------------------


@pytest.mark.parametrize("hostname", ["", "   "])
def test_a_blank_hostname_is_refused(hostname: str) -> None:
    with pytest.raises(ValueError):
        merge_ssh_rule([dict(CATCH_ALL)], hostname, SERVICE)


@pytest.mark.parametrize("service", ["", "   "])
def test_a_blank_service_is_refused(service: str) -> None:
    with pytest.raises(ValueError):
        merge_ssh_rule([dict(CATCH_ALL)], HOST, service)


# --- the helpers the route builds its request bodies with ------------------


def test_ssh_service_uses_port_22_by_default() -> None:
    assert ssh_service("192.0.2.10") == "ssh://192.0.2.10:22"
    assert ssh_service("192.0.2.10", 2222) == "ssh://192.0.2.10:2222"
    assert DEFAULT_SSH_PORT == 22


@pytest.mark.parametrize(("origin", "port"), [("", 22), ("192.0.2.10", 0), ("192.0.2.10", 70000)])
def test_ssh_service_refuses_a_host_or_port_it_cannot_build(origin: str, port: int) -> None:
    with pytest.raises(ValueError):
        ssh_service(origin, port)


def test_tunnel_cname_is_built_from_the_operator_s_tunnel_id() -> None:
    assert tunnel_cname("tunnel-from-the-environment") == (
        "tunnel-from-the-environment.cfargotunnel.com"
    )


def test_tunnel_cname_refuses_an_empty_id() -> None:
    with pytest.raises(ValueError):
        tunnel_cname("  ")


# --- reading the API's own shape -------------------------------------------


def configuration(*rules: dict) -> dict:
    return {"success": True, "result": {"config": {"ingress": list(rules)}}}


def test_tunnel_config_ingress_reads_the_documented_shape() -> None:
    assert tunnel_config_ingress(configuration(UNRELATED, CATCH_ALL)) == [UNRELATED, CATCH_ALL]


def test_tunnel_config_ingress_returns_copies() -> None:
    document = configuration(UNRELATED, CATCH_ALL)

    rules = tunnel_config_ingress(document)
    rules[0]["service"] = "http://changed:1"

    assert document["result"]["config"]["ingress"][0] == UNRELATED


def test_a_failed_response_is_refused_rather_than_read_as_empty() -> None:
    with pytest.raises(ValueError):
        tunnel_config_ingress({"success": False, "errors": [{"message": "nope"}]})


def test_an_empty_ingress_in_the_response_is_refused() -> None:
    with pytest.raises(ValueError):
        tunnel_config_ingress(configuration())
