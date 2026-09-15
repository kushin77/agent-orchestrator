"""Tunnel provision + connector deploy for the remote operator SSH route
(issue #785).

`infra/cloudflare/ao-ssh-access.sh` publishes one hostname through a
*remotely-managed* Cloudflare Tunnel. Before it can publish anything the tunnel
has to exist and a `cloudflared` connector has to be running on a host that can
reach the origin. Those two jobs are the provision path, and until issue #785
the route assumed they were somebody else's problem: `ingress.py` merged a rule
into an *existing* tunnel and deployed *no* connector, while the other half --
tunnel creation and the dual-node connector -- lived in `kushin77/shared-services`
`infra/modules/cloudflare-tunnel/`. That split is the drift this file ends.

The provision path has three properties worth keeping as *pure functions* (so
they are unit-testable and mutation-provable, the same contract `ingress.py`
owes the route):

  * **find-or-create is idempotent.** A tunnel is looked up by name; if the
    list comes back with one, its id is returned and *no create is attempted*;
    only an empty list authorises a create. `tunnel_id_from_list` returns `""`
    for a genuinely empty list and raises on anything unreadable -- the two are
    different facts, and only the first is safe to act on.
  * **the read is fail-closed.** An unreadable, non-success, or mis-shaped
    tunnel-list response is a `ValueError`, never "no tunnel exists". Treating
    "I could not observe the list" as "empty" would authorise a create that
    either collides with an existing tunnel or, worse, re-points a publish at
    the wrong estate.
  * **the connector deploy is idempotent and token-free.** The deploy is a
    `docker pull` + `docker rm -f` + `docker run --restart unless-stopped`, so
    re-running it converges to one running connector. The tunnel token is
    passed with docker's `-e TUNNEL_TOKEN` (inherit-from-environment) form, so
    no token ever appears in the command template this file returns -- the
    token arrives from the environment at apply time only (GR-6).

All I/O -- the API calls, the environment, the SSH hop -- lives in the shell
script. This module imports only the standard library.

PROVENANCE (GR-10): the *pattern* (find-or-create by name, idempotent
`docker rm -f` + `run`, Vault-or-env token posture) is ported from
`kushin77/shared-services` `infra/modules/cloudflare-tunnel/` (issue #1171).
That repo is private, carries no license and is owned by the same owner, so no
code was copied; see `docs/CANNIBALIZATION.md` section 16.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

#: The service a tunnel's trailing catch-all rule uses. A brand-new tunnel is
#: seeded with exactly this one rule, so the publish merge always has a
#: catch-all to land before.
CATCHALL_SERVICE = "http_status:404"


def tunnel_id_from_list(document: Mapping[str, Any]) -> str:
    """Return the id of the first tunnel in a `cfd_tunnel` list response.

    Returns ``""`` only when the response is a *successful* read of a genuinely
    empty list -- "there is no tunnel of that name", which authorises a create.
    Anything unreadable raises `ValueError`, so a failed read can never be
    mistaken for "no tunnel exists" and trigger a create against a blind list.
    """
    if not isinstance(document, Mapping):
        raise ValueError("the tunnel list response is not an object")
    if document.get("success") is not True:
        raise ValueError(
            "the tunnel list could not be read: %r" % (document.get("errors"),)
        )
    result = document.get("result")
    if not isinstance(result, list):
        raise ValueError("the tunnel list response carries no result array")
    for entry in result:
        if not isinstance(entry, Mapping):
            raise ValueError("a tunnel list entry is not an object: %r" % (entry,))
        tunnel_id = entry.get("id")
        if not tunnel_id:
            raise ValueError("a tunnel list entry carries no id")
        return str(tunnel_id)
    return ""


def initial_tunnel_config() -> dict[str, Any]:
    """The seed configuration for a brand-new tunnel: a single catch-all.

    A new tunnel has no ingress, and the publish merge must never be driven
    against an empty, mis-read configuration. Seeding the catch-all here means
    the tunnel always carries at least one rule, so `tunnel_config_ingress`
    (which refuses an empty list) stays honest and the publish merge always has
    a trailing rule to land before.
    """
    return {"config": {"ingress": [{"service": CATCHALL_SERVICE}]}}


def connector_container_name(prefix: str, hostnames: Sequence[str]) -> str:
    """The idempotent connector container name for a prefix + hostname set.

    Normalises the hostname list the same way the shared-services module did
    (join on ``-``, then fold ``.`` and ``,`` to ``-``) so a hostname cannot
    inject characters that break the container name, and prefixes the result.
    """
    value = str(prefix or "").strip()
    if not value:
        raise ValueError("connector container prefix must be non-empty")
    joined = "-".join(str(h).strip() for h in hostnames if str(h).strip())
    for char in (".", ","):
        joined = joined.replace(char, "-")
    return "%s-%s" % (value, joined)


def connector_deploy_lines(
    container: str, image: str, network: str = "bridge"
) -> list[str]:
    """The idempotent `cloudflared` connector deploy, as a command list.

    Converges to exactly one running connector no matter how many times it is
    applied: pull the image, remove any previous container of the same name,
    run it with `--restart unless-stopped`, then inspect it. The tunnel token
    is deliberately *not* a parameter: the `-e TUNNEL_TOKEN` (no ``=``) form
    makes docker inherit the token from the environment, so this template never
    contains it and it can be printed in a dry-run plan without leaking a
    secret (GR-6).
    """
    if not str(container or "").strip():
        raise ValueError("connector container name must be non-empty")
    if not str(image or "").strip():
        raise ValueError("connector image must be non-empty")
    net = str(network or "bridge").strip() or "bridge"
    return [
        "docker pull %s" % image,
        "docker rm -f %s >/dev/null 2>&1 || true" % container,
        "docker run -d --name %s --network %s --restart unless-stopped "
        "-e TUNNEL_TOKEN %s tunnel --no-autoupdate run" % (container, net, image),
        "docker inspect --format '{{.State.Running}}' %s" % container,
    ]
