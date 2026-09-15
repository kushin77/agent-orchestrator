"""Ingress merge for the remote operator SSH route (issue #771).

`infra/cloudflare/ao-ssh-access.sh` publishes one hostname through a
*remotely-managed* Cloudflare Tunnel. The API has no "add one rule" call: the
tunnel's whole `config.ingress` array is rewritten, so a blind replacement
deletes every other hostname the tunnel serves and turns a deploy into an
outage. The merge below is that step, and the only part of the route with
interesting logic:

  * a rule for the requested hostname is updated IN PLACE -- same index, same
    position in the array, never duplicated;
  * a hostname with no rule of its own gets one inserted IMMEDIATELY BEFORE the
    trailing catch-all (the last rule, which carries no `hostname`), so the
    catch-all stays last;
  * every unrelated rule comes back unchanged.

`merge_ssh_rule` is pure: it returns a new list, never mutates its argument and
imports nothing outside the standard library, so it is unit-testable and
mutation-provable (`infra/cloudflare/tests/`, `scripts/check-ao-ssh-access.sh`).
All I/O -- the API calls, the environment, the registry read -- lives in the
shell script.

PROVENANCE (GR-10): the *pattern* (merge, never replace) is ported from
`kushin77/shared-services`, `scripts/deploy-ssh-tunnel-access.sh`. That repo is
private, carries no license and is owned by the same owner, so no code was
copied; see `docs/CANNIBALIZATION.md` section 15.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

#: The port an SSH origin is reached on unless the operator says otherwise.
DEFAULT_SSH_PORT = 22

#: Cloudflare's own CNAME suffix for a tunnel's published hostname. The tunnel
#: id it is prefixed with belongs to the operator's estate and comes from the
#: environment -- never from this tree.
TUNNEL_CNAME_SUFFIX = "cfargotunnel.com"


def ssh_service(origin: str, port: int = DEFAULT_SSH_PORT) -> str:
    """The `service` a tunnel rule reaches an SSH origin with."""
    host = str(origin or "").strip()
    if not host:
        raise ValueError("origin must be a non-empty host")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError("port %r is not a TCP port" % (port,))
    return "ssh://%s:%d" % (host, port)


def tunnel_cname(tunnel_id: str) -> str:
    """The CNAME content the published hostname needs for a given tunnel."""
    value = str(tunnel_id or "").strip()
    if not value:
        raise ValueError("tunnel id must be non-empty")
    return "%s.%s" % (value, TUNNEL_CNAME_SUFFIX)


def tunnel_config_ingress(document: Mapping[str, Any]) -> list[dict]:
    """The ingress list of a tunnel-configurations API response.

    Refuses -- it never returns an empty list -- when the response is not the
    shape the API documents. An empty list read out of a mis-read response
    would make the caller rewrite the tunnel with a single rule, which is the
    same outage this module exists to prevent.
    """
    if not isinstance(document, Mapping):
        raise ValueError("the tunnel configuration response is not an object")
    if document.get("success") is not True:
        raise ValueError(
            "the tunnel configuration could not be read: %r" % (document.get("errors"),)
        )
    result = document.get("result")
    config = result.get("config") if isinstance(result, Mapping) else None
    ingress = config.get("ingress") if isinstance(config, Mapping) else None
    if not isinstance(ingress, list) or not ingress:
        raise ValueError("the tunnel configuration carries no ingress list")
    rules: list[dict] = []
    for rule in ingress:
        if not isinstance(rule, Mapping):
            raise ValueError("an ingress rule is not an object: %r" % (rule,))
        rules.append(dict(rule))
    return rules


def merge_ssh_rule(
    ingress: Sequence[Mapping[str, Any]], hostname: str, service: str
) -> list[dict]:
    """Return a NEW ingress array with `service` serving `hostname`.

    See the module docstring for the three properties this owes the route:
    unrelated rules survive, the same hostname is updated in place, and a new
    rule lands before the trailing catch-all.
    """
    host = str(hostname or "").strip()
    if not host:
        raise ValueError("hostname must be non-empty")
    if not str(service or "").strip():
        raise ValueError("service must be non-empty")

    rules: list[dict] = [copy.deepcopy(dict(rule)) for rule in ingress]
    replacement = {"hostname": host, "service": service}

    for index, existing in enumerate(rules):
        if existing.get("hostname") == host:
            # Same index, same neighbours: update the rule IN PLACE, keeping any
            # option the operator already configured on this hostname's rule --
            # the caller only ever states the service.
            rules[index] = {**existing, **replacement}
            return rules

    # No rule for this hostname yet: insert immediately before the trailing
    # catch-all, so the catch-all stays last. With no catch-all, append.
    if rules and not rules[-1].get("hostname"):
        rules.insert(len(rules) - 1, replacement)
    else:
        rules.append(replacement)
    return rules
