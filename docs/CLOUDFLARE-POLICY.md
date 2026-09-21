# The declarative Cloudflare edge policy — ingress, WAF, DNS (issue #1766)

The single spec for this repository's Cloudflare edge policy: the **current**
ingress, WAF (edge access control) and DNS rules, stated declaratively.

Before this page the policy was **implicit in script logic** — split across
`infra/cloudflare/ingress.py`, `infra/cloudflare/provision.py`,
`infra/cloudflare/ao-ssh-access.sh`, `infra/feature-flags/registry.yaml`, and
five docs that each restated a fragment of it (`docs/EDGE-CUTOVER.md`,
`docs/AGENTCONSOLE-HOSTING.md`, `docs/AGENTCONSOLE-GOLIVE.md`,
`docs/OPERATOR-ACCESS.md`, `docs/CANNIBALIZATION.md`). This page is where the
rules live; those five now **cross-reference** it instead of restating it, and
the domain is registered in the policy registry (`governance/policy/domains/cloudflare.yaml`).

**Grounding rule.** Every rule below names the `file:line` that implements or
declares it. Nothing here is invented; a rule with no implementing line has no
business being called policy. This page is the *source of truth for the rule*,
not a second implementation — the code remains the mechanism.

## 0. What the edge serves today

Exactly **one** route is declared, and it ships OFF:

| Surface | Flag | Default | Posture |
|---|---|---|---|
| `remote_ssh_access` | `surfaces.remote_ssh_access` | `off` | `hold` |

- The flag row is declared in `infra/feature-flags/registry.yaml:539` (`default: off`, `posture: hold`), scoped to `service: control-plane` (`:543`). It covers the **route**, not a listener in this tree.
- The route itself is `infra/cloudflare/ao-ssh-access.sh` — publish one hostname that reaches the host's `sshd` through a **remotely-managed** Cloudflare Tunnel, with Cloudflare Access in front, so an operator reaches a shell without opening port 22 to the internet.

There is no second edge route declared in this repository.

## 1. The rule: nothing is applied before a reviewed go-live (GR-5)

- **Dry run is the default.** `DRY_RUN=true` (`ao-ssh-access.sh:72`); only `--apply` sets it false (`:120`). A dry run performs **no** mutating request — it reads live state and prints exactly what it *would* send.
- **`--apply` refuses while the flag is OFF.** `read_flag` (`:134`) reads `surfaces.remote_ssh_access`; a value other than `on` refuses `--apply` by name (`:156`–`:158`), citing `infra/feature-flags/registry.yaml` and GR-5.
- **The flag read is fail-closed and single-sourced.** `ao-ssh-access.sh:134`–`:152` reads through `portal.server.fleet.read_surface_default` — the one implementation of "is this surface promoted" — and treats an unreadable registry as `off`, never as `on`.

## 2. Ingress rules (the tunnel's `config.ingress`)

The Cloudflare API has **no "add one rule" call**: the tunnel's whole
`config.ingress` array is rewritten, so a blind replacement deletes every other
hostname the tunnel serves. The rules below are how that is prevented.

- **Merge, never replace — and merge is a pure function.** `merge_ssh_rule` (`ingress.py:88`) imports nothing outside the standard library and never mutates its argument; it returns a new list.
- **The same hostname is updated IN PLACE.** A rule whose `hostname` matches is updated at the **same index** with the same neighbours, keeping any option the operator already set on that rule (`ingress.py:105`–`:111`).
- **A new hostname lands immediately BEFORE the trailing catch-all.** A rule with no `hostname` is the catch-all and must stay **last**; a new hostname is inserted at `len(rules) - 1` (`ingress.py:115`); with no catch-all present it is appended.
- **The live read is fail-closed.** `tunnel_config_ingress` (`ingress.py:61`) refuses (never returns an empty list) when the response is not the documented shape — an empty array read from a mis-read response would drive the caller to rewrite the tunnel with a single rule, the exact outage the merge exists to prevent.
- **An SSH rule's service is `ssh://<origin>:<port>`.** `ssh_service` (`ingress.py:43`) formats it; the default port is `22` (`DEFAULT_SSH_PORT`, `ingress.py:35`).
- **A brand-new tunnel is seeded with exactly one catch-all.** `initial_tunnel_config` (`provision.py:81`) returns a single `http_status:404` rule (`CATCHALL_SERVICE`, `provision.py:51`), so the publish merge always has a trailing rule to land before.

### 2.1 Provision: find-or-create is idempotent and the read is fail-closed

- **Find-or-create by name.** `tunnel_id_from_list` (`provision.py:54`) returns the first id in a **successful** read of the tunnel list; a **genuinely empty** list returns `""` and is the only signal that authorises a create.
- **`""` (no tunnel) and "unreadable" are different facts.** A non-success or mis-shaped list raises `ValueError` (`provision.py:65`–`:78`), so "I could not observe the list" can never be mistaken for "no tunnel exists" and re-point a publish at the wrong estate.
- **The connector deploy is idempotent and token-free.** `connector_deploy_lines` (`provision.py:109`) returns `docker pull` + `docker rm -f` + `docker run --restart unless-stopped`, converging to one running connector; the token is passed with docker's `-e TUNNEL_TOKEN` (inherit-from-environment) form (`provision.py:131`), so **no token ever appears in the command template**. Container names are normalised by `connector_container_name` (`provision.py:93`).

## 3. WAF / edge access control (Cloudflare Access)

The edge access control for a published hostname is a **Cloudflare Access
self-hosted application plus an allow-policy**. Without that pair the hostname
is an **unauthenticated public door to `sshd`**, so the route never skips it.

- **Access is always ensured — there is no skip flag.** The route's own header states the rule (`ao-ssh-access.sh:27`–`:29`, `:544`–`:548`): "WITHOUT THIS THE HOSTNAME IS AN UNAUTHENTICATED PUBLIC DOOR to sshd, so there is deliberately no flag that skips it." (The upstream `shared-services` `--no-access` flag was deliberately **not** ported — `docs/CANNIBALIZATION.md` §15.)
- **The application is `self_hosted`, not launcher-visible.** `type: "self_hosted"` and `app_launcher_visible: false` (`ao-ssh-access.sh:573`–`:574`).
- **The allow-policy is `decision: allow` over an explicit operator email list.** `ao-ssh-access.sh:583` builds `include: [{email: …}]` per address.
- **An empty allow-list is refused.** `ao-ssh-access.sh:193` refuses when `AO_SSH_ACCESS_EMAILS` lists no address — "an Access app with an empty policy is not a door worth publishing".
- **An app without an id is refused.** The route never leaves the hostname without a policy: `ao-ssh-access.sh:620` aborts if the Access application has no id.
- **The session lifetime is bounded.** Default `24h` (`SESSION_DURATION`, `ao-ssh-access.sh:190`), carried on both the app and the policy (`:573`, `:583`).

**Declared absence (not an accident).** This repository declares **no separate
Cloudflare WAF ruleset** — no custom-rules or managed-ruleset configuration
exists anywhere under `infra/`. The edge access control *is* the Access
application + allow-policy above. If a WAF ruleset is ever added, its
declaration belongs in this section; until then, the honest statement is that
the WAF layer is the Access policy, and it is not claimed to be more.

## 4. DNS rules

- **The published name is a proxied CNAME to the tunnel.** `<hostname>` → `<tunnel id>.cfargotunnel.com`: `tunnel_cname` (`ingress.py:53`) formats it against `TUNNEL_CNAME_SUFFIX = "cfargotunnel.com"` (`ingress.py:40`); the body is built at `ao-ssh-access.sh:511`–`:521` as `{"type": "CNAME", "proxied": true, "ttl": 1}`.
- **TTL `1` means "automatic" (proxied), not one second.** `ao-ssh-access.sh:521`.
- **Upsert, never duplicate.** The existing record carrying the name is read (`ao-ssh-access.sh:488`–`:508`) and `PUT`; only when none exists is a `POST` issued (`:525`–`:538`).
- **The name is verified after apply.** `ao-ssh-access.sh:676`–`:682` resolves `A`/`AAAA` (and re-reads the live ingress, `:673`) — nothing is reported as applied before it is verified.
- **The hostname must be a bare hostname.** A URL or an address is refused: `ao-ssh-access.sh:182`.

## 5. Identifiers and secrets never live in the tree (GR-6)

- **Every estate identifier comes from the environment, and a missing one is refused BY NAME rather than defaulted.** `AO_SSH_HOSTNAME`, `AO_SSH_ORIGIN_HOST`, `AO_SSH_ACCESS_EMAILS`, `CF_ACCOUNT_ID`, `CF_ZONE_ID`, `CF_TUNNEL_ID` are required at `ao-ssh-access.sh:170`–`:177`; `require_var` refuses a defaulted value because "a default here would point the run at somebody else's estate" (`:44`–`:46`).
- **No estate identifiers in this tree at all** — account id, zone id, tunnel id, origin host, connector hosts and operator emails are environment-only (`ao-ssh-access.sh:44`–`:51`).
- **The API token comes from `CF_API_TOKEN` or GCP Secret Manager only.** `resolve_token` (`:198`–`:217`) accepts `AO_CF_TOKEN_SECRET` + `AO_GCP_SECRET_PROJECT` as the Secret Manager path, and refuses when neither is present (`:217`).
- **The connector's tunnel token is environment-only.** It is required for `--connector --apply` (`:713`) and is shlex-quoted into the remote command at apply time — never embedded in a `file`, never committed (`provision.py:117`–`:131`).

## 6. Enforcement point

The policy **bites at deploy time**: `infra/cloudflare/ao-ssh-access.sh` drives
the flow and the pure merge/provision functions
(`infra/cloudflare/ingress.py`, `infra/cloudflare/provision.py`) produce every
edge mutation. The `--apply` act is an **operator** act — an agent never
applies it — and it is additionally gated by the `surfaces.remote_ssh_access`
flag (§1). The registry row's `enforcement_point` names this.

## 7. Registry

This domain is registered in the policy registry as `cloudflare`:

```yaml
domain: cloudflare
source_file: docs/CLOUDFLARE-POLICY.md
enforcement_point: "deploy-time edge provisioning — infra/cloudflare/provision.py + ingress.py drive ao-ssh-access.sh; --apply is gated by surfaces.remote_ssh_access (GR-5)"
control_plane_visible: true
```

Declaration: `governance/policy/domains/cloudflare.yaml`. Schema and the
one-file-per-domain contract: `governance/policy/README.md`.

## See also (the five docs that cross-reference this page)

- `docs/EDGE-CUTOVER.md` — the decision to front `ai.purebliss.app` with a Cloudflare tunnel.
- `docs/AGENTCONSOLE-HOSTING.md` — where the browser console runs and the source/run handoff.
- `docs/AGENTCONSOLE-GOLIVE.md` — the console go-live cutover (the tunnel ingress merge, two ordered rules).
- `docs/OPERATOR-ACCESS.md` — §6, reaching a shell over the Cloudflare Tunnel.
- `docs/CANNIBALIZATION.md` — §15/§16, provenance for the ported ingress-merge and tunnel-provision patterns.
