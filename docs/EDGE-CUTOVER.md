# Edge cutover — how `ai.purebliss.app` is fronted, and what is retired (issue #731)

> **Status: declaration, not a deployment.** This page declares the fronting for
> `ai.purebliss.app` and the disposition of the GCP edge route. **No cutover has
> happened**: the hostname still answers through its current fronting, and the
> run-half steps in [§5](#5-the-run-half-steps-env-seam-and-named-refusals) are
> **owed, not done.** The Terraform half of the reconciliation is declared and
> flag-gated OFF ([§4](#4-the-retired-route-as-code)); the mechanical gate that
> keeps it from regressing is `scripts/check-edge-cutover.sh`
> ([§9](#9-how-this-cannot-silently-regress)).

Companion pages: [`AGENTCONSOLE-HOSTING.md`](AGENTCONSOLE-HOSTING.md) (where the
console runs and who makes it run — this page's decision is its sibling for the
web hostname), [`OPERATOR-ACCESS.md`](OPERATOR-ACCESS.md) (which command reaches
which surface), [`../portal/README.md`](../portal/README.md) (the portal half).

## 1. The problem this page closes

The hostname `ai.purebliss.app` was claimed by **two declarations that met
nowhere**:

| Declaration | Says the hostname belongs to | Where |
|---|---|---|
| The Terraform edge route | GCP — a Cloud DNS CNAME to Google's hosted load balancer, plus a Cloud Run domain mapping | [`../infra/terraform/modules/web-surface/main.tf`](../infra/terraform/modules/web-surface/main.tf) |
| The live DNS zone | Cloudflare — the zone is served by Cloudflare nameservers | measured, [§3](#3-today-measured) |

Nothing reconciled them. Measured before this lane:
`grep -rniE 'reconcil|cutover|cut over' docs/ scripts/ infra/ portal/ |
grep -iE 'cloudflare|dns|tunnel|domain|hostname|purebliss'` returned **nothing**
— the conflict was real, stated nowhere, and enforced by nothing, so whichever
declaration the next deploy happened to run would silently win.

## 2. The decision: a Cloudflare tunnel to the shared-services run half

**The fronting for `ai.purebliss.app` is a Cloudflare tunnel to the shared-services run half.**
The GCP DNS record and the Cloud Run domain mapping are the **RETIRED route**
and are no longer created by default.

This is not a new decision. It is the decision this repository already recorded
**once, for a sibling surface** — AgentConsole — and this page applies it to the
web hostname rather than inventing a second answer. The three precedents,
verbatim:

**a.** [`AGENTCONSOLE-HOSTING.md:22-29`](AGENTCONSOLE-HOSTING.md) — the owner
directive of 2026-09-04, recorded in shared-frontend `docs/DEPLOYMENT.md`:

> Live hosting of the OS portal shell and the vendored modules runs **only on our
> remote cluster, wired in the `shared-services` module** … This repo is the
> **source + build + registry** half; `shared-services` is the **run** half. A
> Cloud Run deploy pipeline … has been **removed**: it implied a live host that is
> not the live host. **No GCP deploy config lives here.**

**b.** [`../contrib/shared-services/agentconsole.compose.yml:14-17`](../contrib/shared-services/agentconsole.compose.yml)
— the overlay that supersedes the Cloud Run route:

> The console's Cloud Run declaration (`infra/terraform/modules/web-surface`,
> flag-gated OFF and inert) is the RETIRED route; this overlay supersedes it for
> AgentConsole. Nothing here is applied by agent-orchestrator.

**c.** [`AGENTCONSOLE-HOSTING.md:220-224`](AGENTCONSOLE-HOSTING.md) — who owns the
hostname, and the boundary this page respects:

> **The ingress hostname is the run half's decision.** The overlay ships no
> hostname and this page proposes none as fact: DNS, the Cloudflare tunnel
> ingress rule and (if the run half wants the extra hop) an Access policy are
> enabled in the shared-services run half, the way `os.purebliss.app` was.
> Nothing in this repo hardcodes a domain.

The same page already lists this hostname in its live-surface table
([`AGENTCONSOLE-HOSTING.md:209`](AGENTCONSOLE-HOSTING.md)) as
`ai.purebliss.app | oauth2-proxy front over Open WebUI | remote cluster
192.168.168.42`, and its run-half handoff ends with
(`AGENTCONSOLE-HOSTING.md:265`) `Wire DNS + the Cloudflare tunnel ingress rule to
192.168.168.42:<port>`.

Consequences, stated plainly:

- **`ai.purebliss.app` is not a new tunnel and not a new domain.** It is an
  **existing** hostname that must be **re-pointed** by the run half at whatever
  origin serves the SPoG. This repository declares the destination *mechanism*
  (the tunnel the run half already owns) and the *retirement* (no GCP record);
  it does not name a port or an internal address as fact.
- **The mechanism is not invented here either.** It exists and is gate-proven
  already: [`../infra/cloudflare/ao-ssh-access.sh`](../infra/cloudflare/ao-ssh-access.sh)
  (provision the tunnel, publish a hostname, deploy the connector) with the pure
  ingress merge in [`../infra/cloudflare/ingress.py`](../infra/cloudflare/ingress.py),
  proven offline by `scripts/check-ao-ssh-access.sh` against
  [`../infra/cloudflare/stub_cf_api.py`](../infra/cloudflare/stub_cf_api.py)
  bound to `127.0.0.1`. The cutover is that mechanism applied to this hostname.

## 3. Today, measured

Captured `2026-09-16T16:55:54Z` from this checkout. These are the numbers the
close comment for #731 must record, and the baseline the cutover is measured
against.

```
$ dig +short NS purebliss.app
greg.ns.cloudflare.com.
gracie.ns.cloudflare.com.

$ dig +short ai.purebliss.app
104.21.34.108
172.67.159.67

$ dig +short CNAME ai.purebliss.app
                        # empty: proxied A records, no CNAME
```

```
$ curl -sS -I https://ai.purebliss.app/
HTTP/2 302
content-type: text/plain; charset=utf-8
content-length: 33
location: /auth/login
server: cloudflare
cf-ray: a3c166617dc00d33-EWR
```

```
$ curl -sS -w '\nHTTP=%{http_code} BYTES=%{size_download}\n' \
    https://ai.purebliss.app/api/fleet/snapshot
Found. Redirecting to /auth/login
HTTP=302 BYTES=33
```

What that measures, and one thing it does **not**:

- The zone is served by **Cloudflare nameservers**, and the hostname answers from
  **Cloudflare proxy addresses** — so the first half of the decision (Cloudflare
  fronting) is already the live reality, and the retired GCP route is *not* what
  answers today.
- The 302 is returned **before any origin routing**: the response is a 33-byte
  plain-text `Found. Redirecting to /auth/login`, and **none of the headers the
  portal itself sets** appear in it. It is a fronting proxy's redirect, not the
  SPoG's answer — the SPoG's own contract for that path is **401**
  (see [§6](#6-what-731s-acceptance-can-and-cannot-be-satisfied-here)).
- **Which origin answers today is not observable from outside this gate.** An
  unauthenticated caller is redirected by the front proxy, so *the legacy seed and
  the SPoG are indistinguishable from the internet*. This page therefore does not
  claim which one is live, and the cutover's success criterion is not observable
  from here either — which is exactly why §6 exists.

## 4. The retired route, as code

The declaration is only worth anything if the Terraform agrees with it, so the
module's **default** now matches the decision. `create_gcp_edge_route` (default
`false`) gates both halves of the route in
[`../infra/terraform/modules/web-surface/main.tf`](../infra/terraform/modules/web-surface/main.tf):

```hcl
# one decision, expressed once — the record and the mapping cannot disagree
edge_route = var.enabled && var.create_gcp_edge_route ? 1 : 0

resource "google_dns_record_set" "web" {
  count        = local.edge_route          # was: local.create
  managed_zone = local.managed_zone
  rrdatas      = ["ghs.googlehosted.com."] # the GCP target, retired by default
}

resource "google_cloud_run_domain_mapping" "web" {
  count    = local.edge_route              # was: local.create
}
```

Two further incoherences were closed in the same change, because the retirement
made them reachable:

1. **`create_dns_zone = false` named a zone it never resolved.** The record took
   `managed_zone = var.zone_name` while no zone was created and none was looked
   up, so a wrong `zone_name` was invisible until DNS failed to resolve. The
   pre-existing path is now a real lookup
   (`data "google_dns_managed_zone" "existing"`), and `managed_zone` is
   `one(concat(...))` over the created zone and the resolved one. `create_dns_zone`
   also defaults to `false` now — with the route retired, a default deploy has no
   zone to create — and the incoherent combination is **refused by name**:

   ```
   create-dns-zone-orphan: create_dns_zone = true declares a DNS managed zone to
   hold the GCP edge route's record, but create_gcp_edge_route = false means that
   route is RETIRED and no record will be created in it (docs/EDGE-CUTOVER.md).
   ```

2. **The module's own output indexed a resource that is now gated off.**
   `google_cloud_run_domain_mapping.web[0]` is an **invalid index** the moment
   `create_gcp_edge_route = false` and `enabled = true` — the everyday promoted
   posture — so the retired route would have failed at **plan** time rather than
   disappear. The output reads `one(google_cloud_run_domain_mapping.web[*].name)`,
   which is `null` for a mapping that was deliberately not created.

**This is a declaration, not an apply.** `create_gcp_edge_route = true` is the act
that re-declares the GCP route, and nothing here applies either side: the apply
route stays [`../infra/cloudbuild/apply.yaml`](../infra/cloudbuild/apply.yaml) as
the deployer service account (GR-5). No `terraform apply`, no `gcloud`, no
Cloudflare API call, and no console click happened in this lane.

## 5. The run-half steps, env-seam and named refusals

The cutover is an **operator act on the shared-services run half**, executed the
way [`../infra/cloudflare/ao-ssh-access.sh`](../infra/cloudflare/ao-ssh-access.sh)
already works: every estate identifier comes from the **environment**, and a
missing one is **refused by name** rather than defaulted — a default here would
point the run at somebody else's estate. The API token comes from `CF_API_TOKEN`
or GCP Secret Manager only, never a file and never git (GR-6).

| Step | Identifier (env) | Refused by name when missing |
|---|---|---|
| 1. Read the tunnel's **live** ingress config | `CF_ACCOUNT_ID`, `CF_TUNNEL_ID` | `refused: CF_ACCOUNT_ID is required` |
| 2. Merge an `http://<origin>:<port>` rule for the hostname (never replace — see `merge_ssh_rule`) | `AO_EDGE_HOSTNAME`, `AO_EDGE_ORIGIN` | `refused: AO_EDGE_ORIGIN is required` |
| 3. Upsert the proxied CNAME `<hostname>` → `<tunnel id>.cfargotunnel.com` | `CF_ZONE_ID` | `refused: CF_ZONE_ID is required` |
| 4. Point the SPoG origin at the service that serves `/api/fleet/snapshot` | `AO_EDGE_ORIGIN` (port is the **run half's** choice) | `refused: AO_EDGE_ORIGIN is required` |
| 5. Verify: rule present in the live config, name resolves, live edge returns the SPoG's own answer | — | `refused: nothing is reported applied before it is verified` |

Notes that are part of the declaration, not commentary:

- **Do not replace the tunnel's ingress array.** The API has no "add one rule"
  call; a blind rewrite deletes every other hostname the tunnel serves and turns
  a cutover into an outage. The merge is the pure function already shipped and
  gate-proven.
- **The port is not declared here.** `os.purebliss.app` runs the OS portal shell
  on `192.168.168.42:18280` and `18286` is the *proposed* console port; naming a
  port as fact in this repository would hardcode a domain/port the run half owns
  (`AGENTCONSOLE-HOSTING.md:220-224`). The run half picks it and records it.
- **TLS**: Cloudflare terminates TLS at the edge for a proxied hostname, which is
  why the GCP-managed certificate the retired route would have provisioned is not
  needed. A certificate is not a cutover step here.
- **Rollback**: re-point the ingress rule at the previous origin (step 2 again).
  No zone change, no record deletion, no certificate to unwind — the reason this
  fronting is the safer of the two options the issue names.

## 6. What #731's acceptance can and cannot be satisfied here

Issue #731's acceptance, checked item by item against what this lane can honestly
measure:

| # | Acceptance | Status from this lane |
|---|---|---|
| a | The fronting is decided and declared | **SATISFIED** — [§2](#2-the-decision-a-cloudflare-tunnel-to-the-shared-services-run-half), with the code reconciliation in [§4](#4-the-retired-route-as-code) |
| b | The hostname serves the new SPoG routes; TLS valid; the legacy seed is no longer served | **NOT SATISFIED — the run half's to close.** It is not a repository change: it is the ingress re-point in [§5](#5-the-run-half-steps-env-seam-and-named-refusals) |
| c | `dig +short ai.purebliss.app` recorded in the close comment | **SATISFIED** for the *before* state — [§3](#3-today-measured) records it verbatim; the *after* state is owed with (b) |

The `Verify:` command in the issue is measured here, and it does not currently
distinguish anything:

```
$ curl -sSL -o /dev/null -w '%{http_code}\n' https://ai.purebliss.app/
302
$ curl -s https://ai.purebliss.app/api/fleet/snapshot | head
Found. Redirecting to /auth/login
```

Two measured reasons why that probe is **not yet a usable acceptance test**:

1. **Today it never reaches an origin.** The front proxy redirects before
   routing, so the body is a redirect notice rather than any origin's answer.
2. **The SPoG's own contract for that path is 401, not fleet JSON.** Measured in
   [`../portal/tests/test_fleet_access_control.py`](../portal/tests/test_fleet_access_control.py)
   (`AC1 — an unauthenticated GET /api/fleet/snapshot returns 401`, with
   `payload["error"]["code"] == "unauthorized"`), every fleet route requires a
   session. So "fleet JSON, not the seed" is **not observable by an
   unauthenticated curl even after a correct cutover** — it needs a session
   (`os-session-token`), which only the run half can mint. The probe should be
   widened when (b) is closed, and that widening is the run half's call, not
   this repository's.

## 7. The residual boundary

- **This repository owns**: the declaration (this page), the retirement of the
  GCP edge route as code, the Cloudflare mechanism it reuses, and the gate that
  refuses a regression of the posture.
- **The run half (`kushin77/shared-services`) owns**: the tunnel, the ingress
  rule, the origin and its port, the SPoG service's presence in the live compose
  stack, and the DNS answer for the hostname. **Nothing here is applied by
  agent-orchestrator**, and nothing here can make the hostname serve anything.
- **The owner owns**: the go-live act and any Cloudflare Access policy in front of
  the SPoG. Handover to the run half is by a **direction issue on that board**,
  never by editing that repository from here.
- Explicitly **not** claimed: that a cutover has happened, that the SPoG is live
  at this hostname, that TLS is valid for the new origin, or that the legacy seed
  is gone. None of those is established by this lane.

## 8. Deliberately not done in this lane

Registering an `infra/feature-flags/registry.yaml` row for this surface, and the
matching `infra/rollout/rollout-state.yaml` / `infra/rollout/go-live-plan.yaml`
entries, are **deliberately not done here**: a sibling lane owns
`infra/rollout/**` and the registry is a contended file, so an edit from this lane
would be a file-disjointness violation (AGENTS.md rule 2 / rule 21) for a change
the surface's own promotion lane will make anyway. The promotion posture for this
declaration is therefore:

- the **Terraform** side is flag-gated OFF by default and needs no registry row to
  stay inert (`create_gcp_edge_route = false`, `enabled = false`);
- the **run-half** side is not gated by a flag in this repository at all — it is
  an operator act in the other repository ([§5](#5-the-run-half-steps-env-seam-and-named-refusals));
- if a registry row is wanted for `edge_cutover`, it belongs to the lane that owns
  `infra/feature-flags/registry.yaml` and `infra/rollout/**`, so it is raised here
  as a follow-on rather than performed.

## 9. How this cannot silently regress

`scripts/check-edge-cutover.sh` is the mechanical half of this page, wired into
`make verify` by the discovery layer (`scripts/verify.sh` sources
`scripts/discover-checks.sh`), and it refuses **by name**:

| Finding | What it refuses |
|---|---|
| `declaration-missing` | the declaration is gone |
| `declaration-silent` | the page stops declaring the fronting, the retirement, the retired target, the code symbols |
| `edge-route-default-on` | `create_gcp_edge_route` is defaulted back to `true` |
| `dns-record-ungated` / `domain-mapping-ungated` | either half of the retired route is created unconditionally again |
| `existing-zone-unresolved` | the pre-existing-zone path goes back to naming a zone it never resolves |
| `zone-validation-missing` | the `create-dns-zone-orphan` refusal is removed |
| `output-indexes-gated-resource` | an output indexes a resource the route gate can empty (`web[0]`), which fails at plan time instead of disappearing |
| `incoherent-combination-accepted` | `terraform validate` accepts `create_dns_zone = true` with the route retired |

The **default, in-`make verify` path is entirely offline and deterministic**: it
reads the declaration and the module as text and runs no network call. A live
probe is available as `bash scripts/check-edge-cutover.sh --live` (or by setting
`AO_EDGE_HOST`), which reports the measured DNS records, the HTTP status, the
`server:` header and the acceptance body — and exits **2, CANNOT-ASSESS**, when it
cannot measure, never 0. That mode is never on the `make verify` path, and it
returns **1** while the cutover is outstanding, which is the honest answer today.

The gate proves it can fail: it mutates a copy of the declaration and of the module
one property at a time, and requires each mutation to be refused **by name** — a
check that cannot fail is a formality (GR-12).
