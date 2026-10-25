# Edge cutover — how `ai.purebliss.app` is fronted, and what is retired (issue #731)

> **Status: live front door measured; authenticated content remains gated.** On
> 2026-10-05, `ai.purebliss.app` resolved through Cloudflare; `GET /` redirected
> to `/auth/login`, `/healthz` returned 200, and the unauthenticated fleet
> snapshot returned the portal's JSON 401 (`missing console session token`).
> The shared-services run half records the live OS-auth-gate cutover and retires
> Open WebUI as the public hostname owner. These probes establish the front door
> and protected API route, not the signed-in page or laptop host card; those
> remain separate acceptance work. This repository declares and tests the
> retired GCP route but does not apply the run-half cutover.

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

The older [`AGENTCONSOLE-HOSTING.md`](AGENTCONSOLE-HOSTING.md) entry that called
this hostname an oauth2-proxy front over Open WebUI is historical. The current
hosting declaration names the shared-services OS auth gate as the front door;
the Open WebUI public-hostname path is retired (shared-services #4909).

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

The ingress / WAF / DNS rules that mechanism obeys are stated once in
[`CLOUDFLARE-POLICY.md`](CLOUDFLARE-POLICY.md) — that page is the policy; this
section does not restate it.

## 3. Today, measured (2026-10-05)

These read-only probes were run against the public hostname. The DNS and route
checks agree with the shared-services run-half declaration.

```
$ dig +short NS purebliss.app
greg.ns.cloudflare.com.
gracie.ns.cloudflare.com.

$ dig +short ai.purebliss.app
172.67.159.67
104.21.34.108

$ dig +short CNAME ai.purebliss.app
                        # empty: Cloudflare-proxied A records
```

```
$ curl -sS -D - --max-time 10 https://ai.purebliss.app/
HTTP/2 302
location: /auth/login
server: cloudflare
```

```
$ curl -sS --max-time 10 https://ai.purebliss.app/healthz
{"ok":true}

$ curl -sS -D - --max-time 10 https://ai.purebliss.app/api/fleet/snapshot
HTTP/2 401
{"ok":false,"status":401,"data":null,
 "error":{"code":"unauthorized","message":"missing console session token"}}
```

```
$ bash scripts/check-edge-cutover.sh --live
fronting=cloudflare (declared: cloudflare)  acceptance(/api/fleet/snapshot JSON)=met
check-edge-cutover: OK — the fronting matches the declaration and the SPoG answers
```

The script's displayed `status: 501` is its `HEAD /` measurement; the browser
uses `GET /`, which returns the auth-gate redirect above. The API check is a GET
and its JSON 401 is the expected session boundary, not a failed origin. Do not
conflate HEAD and GET when interpreting the probe.

This confirms Cloudflare fronting and that the protected fleet API is reachable
behind the shared-services OS auth gate. The live shared-services runbook records
the same cutover and says the Open WebUI/Ollama services remain on private ports
while their public hostname path is retired. Anonymous probes do **not** establish
what an authenticated browser renders, nor whether the laptop card is present;
that is tracked separately by agent-orchestrator #2087 and local-laptop #25.

The 2026-09-16 capture in the preceding revision of this document is the
**pre-cutover baseline**: at that time the unauthenticated request was redirected
before the portal API could be observed. It is superseded by the measurements
above.

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

**Current state:** these are the run-half deployment and rollback steps, not
outstanding cutover work. `shared-services` records `enable_os_ai_gate = true`
and `enable_oauth2_proxy_ai = false`; the current public probes in [§3](#3-today-measured-2026-10-05)
confirm the OS auth-gate redirect, health endpoint and protected fleet API.
Open WebUI remains a private workload, not the public hostname owner.

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

Issue #731's acceptance, checked against the current live measurements:

| # | Acceptance | Status |
|---|---|---|
| a | The fronting is decided and declared | **SATISFIED** — Cloudflare to the shared-services run half; the public application front door is the OS auth gate, not Open WebUI. |
| b | The hostname serves the new SPoG routes; TLS valid; the legacy seed is no longer served | **FRONT DOOR SATISFIED; authenticated content not independently observed.** HTTPS validation succeeded; `GET /` redirects to `/auth/login`, `/healthz` returns 200 and the fleet snapshot returns the expected JSON 401. The run-half record says the Open WebUI hostname path is retired. Anonymous probes cannot prove the signed-in page or data contents. |
| c | `dig +short ai.purebliss.app` recorded in the close comment | **SATISFIED** — the current DNS answers are recorded in [§3](#3-today-measured-2026-10-05). |

The earlier 2026-09-16 probe did not distinguish the origin because the request
was redirected before reaching the fleet API. The current probe does: the live
endpoint returns the portal's JSON 401 with `missing console session token`,
matching the unauthenticated contract in
[`../portal/tests/test_fleet_access_control.py`](../portal/tests/test_fleet_access_control.py).
The JSON error is the API's expected auth boundary, not a successful anonymous
data read. The signed-in page and laptop host entry remain separately verifiable
work under #2087 and local-laptop #25.

## 7. The residual boundary

- **This repository owns**: the declaration (this page), the retirement of the
  GCP edge route as code, the Cloudflare mechanism it reuses, and the gate that
  refuses a regression of the posture.
- **The run half (`kushin77/shared-services`) owns**: the tunnel, the ingress
  rule, the origin and its port, the SPoG service's presence in the live compose
  stack, and the DNS answer for the hostname. The OS auth-gate cutover is now
  recorded and externally measured; **nothing here is applied by
  agent-orchestrator**. An authenticated browser session is still required to
  verify the rendered fleet data.
- **The owner owns**: the go-live act and any Cloudflare Access policy in front of
  the SPoG. Handover to the run half is by a **direction issue on that board**,
  never by editing that repository from here.
- Explicitly **not** claimed: that the signed-in fleet page renders the intended
  data, or that the laptop host card is present. The anonymous live probe verifies
  the gate and protected API boundary only; the host-card issue is tracked
  separately.

## 8. Deliberately not done in this lane

Registering an `infra/feature-flags/registry.yaml` row for this surface, and the
matching `infra/rollout/rollout-state.yaml` / `infra/rollout/go-live-plan.yaml`
entries, are **deliberately not done here**: a sibling lane owns
`infra/rollout/**` and the registry is a contended file, so an edit from this lane
would be a file-disjointness violation (AGENTS.md rule 2 / rule 21) for a change
the surface's own promotion lane will make anyway. The promotion posture for this
declaration is therefore:

- the **Terraform** side's `enabled` flag is ON by default (AO-GR-6), but
  `create_gcp_edge_route` itself is a cited owner exception: the route it
  gates is RETIRED (issue #731) in favor of the Cloudflare tunnel, so it
  defaults `false` and `scripts/check-edge-cutover.sh` gates against it ever
  defaulting back to `true` (`create_gcp_edge_route = false`, `enabled =
  true`) — no registry row is needed to keep it inert;
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
returned **0** on 2026-10-05: Cloudflare matched the declaration and the fleet
API returned its expected JSON auth response. A later live failure means the
measured fronting or acceptance body no longer matches the contract; it is not a
signal to assume the cutover is still pending.

The gate proves it can fail: it mutates a copy of the declaration and of the module
one property at a time, and requires each mutation to be refused **by name** — a
check that cannot fail is a formality (GR-12).
