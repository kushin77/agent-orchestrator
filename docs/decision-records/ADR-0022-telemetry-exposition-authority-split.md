---
id: ADR-0022
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0022: The telemetry-exposition authority split + the no-second-dashboard rule

## Status

`accepted` — ratified on the PR for issue #495, the keystone child of EPIC #494
("Consume the fleet monitoring plane"). It freezes, **before any lane writes
code**, how this control plane's telemetry leaves the process, who owns each
surface on the way out, and which vocabulary may not be re-declared at the
boundary. It supersedes no earlier decision. It operates strictly inside the
boundaries already frozen by
[`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md) (the ticket is the
single join node), [`ADR-0010`](ADR-0010-canonical-copy-ownership.md) (one
canonical home; others reference — extended here from *copies* to *contracts*),
and [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (*map the policy, do not
couple the runtime*), whose transport reasoning is re-used rather than
re-derived.

Downstream consumers of this record: #496 (declare the integration +
`docs/OBSERVABILITY.md`), #497 (the exposition surface over the SLO / budget /
metering feed), #498 (the fleet-health publisher). All three are blocked on the
five decisions below and must consume them verbatim rather than re-deciding.

**Numbering note (issue #495 named `ADR-0022`).** Enumerated 2026-09-14 on the
lane base (`origin/master` `07b3d5d`): `docs/decision-records/` carries
`ADR-0001`–`ADR-0018` as files — the issue's premise that the highest file was
`ADR-0012` was already stale by the time this lane opened, because the
in-flight diagrams and codeidx lanes have since landed as `ADR-0017` and
`ADR-0018`. `ADR-0019`–`ADR-0021` are **CMR-hub** numbers cited from this repo
with an explicit `CMR (fleet)` prefix
(`governance/merge/README.md`: `ADR-0020` solo-dev merge posture, `ADR-0021`
SME-reviewer-per-PR; `ADR-0019` the shared-governance dual-role record cited by
#445), so they are not this repo's to take. `ADR-0022` is free as a **file**
and is already allocated to this decision on the board: sibling decision lane
#501 states it explicitly — *"`ADR-0022` is claimed by the monitoring decision
`#495`"* — and takes `ADR-0023` itself; EPIC #494 and issues #497/#498 all cite
`ADR-0022` by name. No open PR or issue claims the number otherwise. This record
therefore lands as `ADR-0022`. (The hub carries an unrelated
`ADR-0022-finops-doctrine`; as with `0020`/`0021`, a hub number is always cited
with its repo prefix and never silently merged with this repo's sequence.)

## Context

This repo is, today, **a signal producer nobody scrapes**. The telemetry pillar
is real and complete — [`slos.py`](../../telemetry/observability/slos.py) with a
closed SLO kind + verdict vocabulary, [`exporter.py`](../../telemetry/budgets/exporter.py)
(`BudgetStateExporter`, described in-tree as the *"SLO feed for tenant
dashboards/alerting"*), the metering rate cards, the audit ledger, the fleet
heartbeats — and **none of it can leave the process**: the only outputs are local
JSONL files. A repo-wide search for Prometheus/Grafana/Jaeger/OpenTelemetry/
Alertmanager instrumentation finds no exporter, no OTLP SDK and no scrape
endpoint. `module.json` declares no monitoring integration. There is no
`docs/OBSERVABILITY.md` and no monitoring declaration anywhere in the tree.

That gap is being closed by EPIC #494 in four lanes at once, which is exactly the
condition under which three lanes would each invent a transport and produce three
incompatible imaginaries of "our metrics". The one thing that **cannot** be
picked later — because every lane's code depends on it and the fleet monitoring
plane must be configured to match it — is the exposition boundary itself. This
record picks it.

**The measurement that forces the decision.** The control plane's runtime is
declared in [`control-plane-service/main.tf`](../../infra/terraform/modules/control-plane-service/main.tf):
`google_cloud_run_v2_service` — a **serverless Google Cloud service**, count-gated
on `var.enabled` (with every flag closed it creates nothing), ingress **internal by
default** (`var.ingress`), and deployed only through the flag-gated Cloud Build
apply route ([`infra/cloudbuild/`](../../infra/cloudbuild/README.md), both triggers
`disabled: true`). It is **not** a Kubernetes pod: there is no pod, no sidecar
injection, no annotation surface and no service monitor to write.

**The vendor's documented default assumes the opposite runtime.** On
`kushin77/monitoring-stack`'s own board,
[`docs/app-instrumentation-guide.md`](https://github.com/kushin77/monitoring-stack/blob/main/docs/app-instrumentation-guide.md)
instructs a service to expose `/metrics` on port `8000` and be **discovered via
Kubernetes pod/service annotations** (`prometheus.io/scrape: "true"`,
`prometheus.io/path: "/metrics"`), with `kubectl` used to verify — a discovery
contract this runtime cannot satisfy. Its
[`docs/INTEGRATION.md`](https://github.com/kushin77/monitoring-stack/blob/main/docs/INTEGRATION.md)
also documents plane-side `scrape_configs` and remote-write centralisation. This
ADR **consumes** those documents and their `sli-definition-templates.md` /
`alert-templates.md` siblings; it does **not** restate the plane's alert or SLO
schema, and it invents no contract the vendor has not published.

**The vendor has ratified the authority split in its own words.** The CMR hub's
**committed** catalog entry for `monitoring-stack`
(`catalog/modules/monitoring-stack/module.json`, hub `HEAD` `dfa3f9b`, clean
working tree) states, ratified by CMR `ADR-0042`:

> `kushin77/shared-services` owns the fleet's single running observability
> instance (Prometheus/Grafana/Alertmanager/Loki/Jaeger) and every rule, SLO and
> dashboard describing the services it operates. `monitoring-stack` stands up no
> second runtime; its GCP-scoped dashboards and alert rules are loaded into the
> shared-services Grafana/Alertmanager.

So the split this ADR freezes is not this repo's invention; it is the vendor's own
declared boundary, and this repo is the missing consumer on the producing end of
it. The hub's `kushin77/monitoring-stack#200` (open, filed by EPIC #494) asks the
vendor the corresponding question from the consumer side — scrape endpoint vs
OTLP push vs remote-write for a **non-Kubernetes** Google Cloud service — and
answers "which Prometheus survives the `infrastructure-monitoring` decommission".
Both answers are the plane's to give; this record therefore fixes what this repo
**owes** without pre-empting what the plane will **name**.

### Decision space (options weighed)

| Question | Options | Chosen |
|---|---|---|
| Direction | pull (scrape endpoint) · push (producer → plane) | **push** |
| Encoding | OTLP/HTTP · Prometheus remote-write · both, configurable | **OTLP/HTTP default, remote-write accepted alternate, one content model** |
| Config owner | this repo ships scrape config · the plane holds it | **the plane** |
| Integration identity | pin a vendor module id · declare the integration in our own shape | **declare, never pin (nothing exists to pin)** |
| Verdict handling | re-declare/coerce at the boundary · import and render verbatim | **import and render verbatim** |
| Label set | rich identity labels · closed bounded dimensions only | **closed bounded dimensions only** |

## Decision

### D1 — The authority split (four surfaces, one direction)

1. **This repo produces.** `telemetry/observability/slos.py` is the **single
   authority** for SLO *kinds* and *verdicts*; `telemetry/budgets/exporter.py` for
   budget/quota/audit state; `telemetry/metering/` for usage. This repo owns the
   rendering of those facts and the delivery of them. It owns nothing else on the
   way out.
2. **`kushin77/shared-services` owns the Prometheus plane.** Its `observability`
   capability (flag `sharedservices.observability`, *"Operational telemetry,
   alerting, and SLO surface for shared platform services"*) is the fleet's single
   running observability instance. This repo never runs a second one.
3. **`kushin77/monitoring-stack` owns aggregation, alerting, routing, runbooks,
   the SLO framework and the dashboards.** Alert names, severities, routes,
   receivers, paging and runbooks are that repo's; this repo never authors or
   renames them.
4. **A signal drives a ticket, never a silent action.** The single join node is
   the ticket ([`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md)). The
   direction is one-way and permanent: **signal → ticket → human**. Nothing in
   the monitoring path writes fleet state — no `fleet/**` file, no `.fleet/**`
   record, no claim, no branch, no worktree is mutated by an export.

**Binding refusals (the "will not do" list, carried from EPIC #494).** This repo
will **never**:

- run a second **Prometheus, Grafana, Alertmanager, Thanos, Loki or Jaeger** —
  not as a service, not as a sidecar, not in `infra/`;
- **build a second dashboard**. The human operator surface is the **web single
  pane of glass** owned by
  [`docs/FLEET-DASHBOARD-GAP-ANALYSIS.md`](../FLEET-DASHBOARD-GAP-ANALYSIS.md)
  (#330 → #331/#332/#333). The monitoring plane is the **machine** surface; the
  SPoG is the **human** surface; re-deriving either from the other is the failure
  this EPIC exists to prevent. No `dashboards/` directory, no Grafana JSON, no
  bundled Grafana;
- **mirror, fork or vendor** the monitoring module (GR-10). Needs go out as a
  direction issue on the vendor board, never as an edit to their files.

**Honest note on the in-tree dashboard.** [`telemetry/observability/dashboard.py`](../../telemetry/observability/dashboard.py)
already generates an **offline, per-tenant** HTML + JSON artifact from the same
verdicts (issue #32; *"no external dashboard server and no network/CDN
dependencies"*). That is a producer-side render of the producer's own data, not a
fleet dashboard plane, and this ADR does not disturb it. Its constraint is
narrow and binding: it stays **offline** and **per-tenant**, and it is never
promoted into the fleet operator surface named in the binding refusals above, nor
pointed at another tenant's data.

### D2 — The transport: push, not pull (decided against the actual runtime)

**Frozen: the direction is push, from the producer to the plane.** The service
pushes; the plane never has to discover, reach into, or maintain a route to the
service.

**Encoding.** The shipped default is **OTLP/HTTP**; **Prometheus remote-write is
the accepted alternate encoding**. The choice is a single **plane-owned
configuration value**, not two code paths and never two vocabularies — both
encodings must render the *same* frozen content model (D4, D5), so switching
encodings cannot fork a verdict, a label or a signal name.

**Why not a scrape endpoint** (each reason measured, not assumed):

- The runtime is Cloud Run v2
  ([`main.tf`](../../infra/terraform/modules/control-plane-service/main.tf)):
  instances are **ephemeral and scale to zero**, there is no pod, no injected
  sidecar, no `ServiceMonitor`/`PodMonitor` CRD to write, and the ingress is
  **internal by default** and inert until a flag promotes the service.
- The vendor's documented default discovery path is **Kubernetes annotations**
  (`app-instrumentation-guide.md`), which this runtime cannot satisfy. Consuming
  it would mean silently reinterpreting the vendor's contract.
- A pull surface **imposes configuration on the plane** — a private-network path
  (VPC connector / PSC) plus explicit target registration — which inverts D1:
  the plane owns the scrape/remote-write config, so the producer must not design
  itself in a way that forces the plane to grow a target list to observe it.
- A pull surface **cannot publish an idle window**. A service scaled to zero has
  no target, so "we could not evaluate this SLO" would arrive at the plane as
  *absence of series*, the very ambiguity D4 exists to forbid. A periodic push
  can publish `NO_DATA` explicitly.
- A scrape surface would add an **unauthenticated HTTP route** to a service that
  exposes none today; push needs no route gate because there is no route.

**Who holds what.**

| Artefact | Owner |
|---|---|
| Scrape / remote-write / ingestion config, endpoint, credentials, retention, recording rules, alert rules, routes, runbooks | **the plane** (`shared-services` / `monitoring-stack`) |
| The rendering of the SLO / budget / metering facts and the push that carries them | **this repo** |

**What this repo owes** (binding on #497 and #498):

- a **flag-gated OFF** push exporter (GR-5). It ships inert; promotion is a
  reviewed flag change, never an implicit default;
- the endpoint and any credential come from **environment or a secret manager
  only** (GR-6) — never committed, never defaulted to a real URL in code;
- **inert when unconfigured**: no endpoint means no export, no retry storm, and
  **never a fabricated healthy signal**;
- a **periodic render**, so a window that could not be evaluated is published as
  `NO_DATA` rather than vanishing;
- **no HTTP exposition route** (so the repo's "route gate sits before authN" rule
  does not apply here — there is no route to gate);
- **no** collector deployment, agent deployment, service discovery, scrape
  config, recording rules, alert rules or dashboards;
- the local JSONL stores remain the **local truth**; the export is a *feed* of
  them, never their replacement, and a delivery failure is reported honestly (it
  is not a silent success, and it is not an SLO verdict).

**Consumption, never derivation.** The **canonical target** is the plane's to
name; `kushin77/monitoring-stack#200` is the open direction issue that will name
it and that resolves which Prometheus survives the `infrastructure-monitoring`
decommission. Because this repo targets *the endpoint the plane names* — and not
a named Prometheus instance — that decommission is **transparent to this repo**:
a change of target is a configuration change, not a code change. This ADR
consumes `INTEGRATION.md`, `app-instrumentation-guide.md` and
`sli-definition-templates.md`; it never restates their schema, and it treats a
vendor reference as prose (board numbers collide across repos, so a vendor issue
is never a `Blocked-by:` marker).

### D3 — The integration identity (declare the integration; pin nothing)

`module.json` `integrations[]` gains exactly one entry, in the **flat shape this
repo's own manifest already uses** (`{ "id": …, "type": … }` — the same shape the
vendor catalog's integration entries take):

```json
{ "id": "prometheus", "type": "monitoring" }
```

The **capability that ties it back** is `sharedservices.observability`, owned by
`kushin77/shared-services` — *"Operational telemetry, alerting, and SLO surface
for shared platform services"*. Because this repo's integration entries carry
only `id` and `type`, the capability tie-back is recorded in the **declaration
document** (`docs/OBSERVABILITY.md`, #496), not smuggled in as an extra key in
`module.json`; the hub's richer entry form (`role`, `evidence[]`) is the hub's
own, and inventing keys here would be a shape this repo does not use.

**No pinned vendor id exists, and none may be invented.** Measured 2026-09-14:

- At this repo's **pinned** CMR revision (`vendor/CMR` gitlink
  `b6c49aa03992dba9fe4b87b46104b8fc2f69f224`), `catalog/mandatory.tsv` carries
  exactly **three** rows — `code-indexing`, `diagrams`, `shared-frontend` — and
  `catalog/modules/` contains `code-indexing`, `diagrams`, `erp-crm`,
  `googleworkspace`, `saas-rbac`, `shared-frontend`. There is **no
  `shared-services` and no `monitoring` module at the pin at all**.
- At the CMR hub's current `HEAD` (`dfa3f9b`), the mandatory set has grown to
  **five** rows (`shared-governance` and `pmo` added) and
  `catalog/modules/monitoring-stack/` now exists — but it is `mandatory: false`,
  `type: "infra"`, with `capabilities: null` and no `prometheus` integration.
- **Either way, monitoring is not a GR-18 mandatory module**, there is **no
  `vendor/CMR/catalog/modules/monitoring/` manifest**, and there is therefore
  **nothing to pin**. The entry carries no `pinned_ref`, no `module_id` and no
  invented vendor id. Monitoring has no `mandatory_consumer_assets`, so this
  program carries no consumer-asset lane — declaring an `.mcp.json`-shaped
  artifact for it would be a false claim about a vendor contract that does not
  exist.

**Provenance note (the EPIC's cited shape).** EPIC #494/#495 cite
`vendor/CMR/catalog/modules/shared-services/module.json:52` as
`{ "id": "prometheus", "type": "monitoring" }` with an `observability` capability
at line 44 (`"default": "on"`, `flags: ["sharedservices.observability"]`).
Reproduced on this machine 2026-09-14, that file **exists on disk** in the shared
checkout's submodule — but it is **untracked there**: `git ls-files` returns
nothing for it, `git log --all` finds **zero** commits containing it, and
`git status` reports it `??` at submodule `HEAD` `b6c49aa`. It is an in-flight,
uncommitted CMR artefact, **not** a pinned declaration. What this ADR takes from
it is the **shape** (which is independently the shape this repo's `module.json`
already uses and the shape the hub's committed integration entries use); it does
**not** pin against the file, and `sharedservices.observability` is recorded as
the EPIC's vendor citation, not restated as a repo-owned contract. When CMR
commits a `shared-services` module, re-reading it is that lane's job, not this
record's silent edit.

### D4 — The SLO-vocabulary boundary (import, render verbatim, never coerce)

[`slos.py`](../../telemetry/observability/slos.py) owns two **closed**
vocabularies, as `frozenset`s:

- kinds: `availability`, `latency`, `cost` (`SLO_KINDS`);
- verdicts: `OK`, `AT_RISK`, `BREACHED`, `NO_DATA` (`VERDICTS`).

**Frozen rules:**

1. **Import, never re-declare.** The export imports those vocabularies. A second
   literal `"BREACHED"` (or `"OK"` / `"AT_RISK"` / `"NO_DATA"`) defined at the
   export boundary is a defect, not a convenience. #497's gate proves it; this
   record fixes the rule the gate enforces.
2. **Verbatim or defective.** The verdict travels **as the literal token**
   `slos.py` produced. A `BREACHED` that leaves the process as anything else —
   renamed, mapped to a severity, coerced to a numeric code, folded into `OK`,
   or dropped — **is a defect**.
3. **No second opinion.** The export renders the verdict `slos.py` **computed**.
   It never re-derives a verdict from raw counters, so the plane can never
   receive two different answers to the same question. Measurements (ratios,
   percentiles, spend) may be exported as *measurements*; they are never
   exported as *verdicts*.
4. **`NO_DATA` is first-class.** It is published, never omitted, and it is never
   read as health. A window that could not be evaluated ends as `NO_DATA` — the
   same honesty `slos.py` already encodes (*"a silent tenant alerts; it is never
   assumed healthy"*) and that `make slo-eval` already enforces by exiting
   non-zero on `BREACHED`/`AT_RISK`/`NO_DATA`. When the producer itself is off,
   the truthful signal is **absence**, and absence is never `OK`; the plane's
   rules must not read it as one, and this record says so explicitly so the
   plane can encode it.

### D5 — The alert identity: a closed, bounded label set (never an id)

Cardinality is a hard constraint, and the vendor has already paid for the lesson:
`kushin77/monitoring-stack#178` — *"[HIGH] Zero Metric Cardinality Management —
One Bad Metric = Prometheus Death"* — is **closed/implemented**, and what it
delivered is exactly a rule about identity: drop the id-shaped dimensions
(`pod_id`, `request_id`, `trace_id`), keep the useful ones (`service`,
`namespace`, `region`), and cap series per job (50K), per instance (10K) and
globally (1M). This repo's label set is chosen so that the plane's relabel-drop
becomes a **no-op for our series** rather than a rescue.

**The identity label set is closed and may contain only these dimensions:**

| Label | Values | Bound |
|---|---|---|
| `service` | constant `agent-orchestrator` | 1 |
| `slo_kind` | one of `SLO_KINDS` | 3 |
| `slo_id` | the declared SLO's own name from `telemetry/observability/slo_templates/**` (`availability-requests`, `latency-p95`, `cost-budget`, …) | the declared template set |
| `verdict` | one of `VERDICTS`, as the literal token | 4 |
| `tenant` | **only** a tenant id that exists in the identity layer's declared tenant registry | the tenant count |

That set **is** the alert identity: `{service, tenant, slo_kind, slo_id}` plus
the verbatim verdict is everything this repo tells the plane about *which* SLO
broke. This repo **never** names an alert, a severity, a route, a receiver, a
paging target or a runbook (D1.3) — those are the plane's, and the plane's SLO
framework keeps naming them. What travels is the *identity of the SLO*, not an
alert definition.

**Refused as identity — a defect, not a style preference:** request/span/trace/
run/session ids, agent ids or names, model or prompt text, free-form error
strings, HTTP paths carrying ids, commit SHAs, host/instance/pod identity,
timestamps used as labels, and any other unbounded or user-controlled value.
These are precisely the dimensions `#178` drops at the plane; refusing them here
means the plane's drop rule never has to catch one of ours.

**Fleet-health signals obey the same rule with the same force (#498).** The
fleet family publishes **counts and closed-set states** — rung state, beat-age
bucket, orphan outcome (`reclaimed` / `parked` / `shelved` / `suspect`), watchdog
verdict — and must **not** carry session ids, worktree paths, branch names, agent
ids or host names. **A session id is this fleet's `pod_id`**, and `#178` drops it
for exactly this reason. Per-session detail belongs to the ticket/log path, never
to a metric label.

### Scope of this record (what it does not decide)

It does not federate module state (#445 owns the "one view of every module"
surface, and it never mentions this ADR's signals); it does not restate the
plane's alert or SLO schema; it does not name the canonical endpoint (the
plane's); it changes no Terraform, no Cloud Build config, no feature-flag
registry, no `Makefile` and no gate script. It decides the boundary and stops.

## Consequences

- **Positive.** Three blocked lanes now have one frozen target instead of three
  invented ones. The transport matches the real runtime instead of a Kubernetes
  assumption, so no lane has to write a service monitor for a pod that does not
  exist. The verdict vocabulary cannot fork, because it is imported from one
  module and rendered verbatim. The label set is bounded by construction, so this
  repo cannot be the source of a cardinality incident of the kind `#178` exists
  to prevent. Because the repo targets "the endpoint the plane names", the
  `infrastructure-monitoring` decommission and the eventual `#200` answer are
  configuration, not migrations.
- **Negative.** Push means this repo carries a delivery concern it would not
  carry if the plane pulled: an endpoint to configure, a failure path that must be
  reported honestly, and a periodic render loop that must run even when the
  service is otherwise idle. It also means this repo must accept that a
  flag-OFF producer is *invisible* to the plane — the honest and intended
  consequence of GR-5, but a fact operators will meet as "there are no series
  yet".
- **Neutral.** Nothing about storage changes: the JSONL stores stay the local
  truth and the export is a feed of them. The exporter is additive and OFF. The
  in-tree offline dashboard is untouched. No new always-on service appears
  anywhere.

### Follow-ups (owned elsewhere; listed so they are not silently dropped)

- **#496** declares the `integrations[]` entry from D3 and writes
  `docs/OBSERVABILITY.md` naming the producer/consumer boundary and the
  `sharedservices.observability` tie-back, pointing at
  [`docs/FLEET-DASHBOARD-GAP-ANALYSIS.md`](../FLEET-DASHBOARD-GAP-ANALYSIS.md)
  as the *different* gap, with a mutation-proven gate.
- **#497** implements the flag-gated-OFF push exporter in D2's transport,
  rendering D4's vocabulary verbatim and D5's label set exactly, with a gate that
  proves **no vocabulary copy**, negative controls that prove `BREACHED` and
  `NO_DATA` leave the process unchanged, tenant scoping, and the cardinality
  bound.
- **#498** implements the fleet-health publisher with D5's closed-set-only rule
  (no session ids) and a negative control per refused shape — a stale beat
  publishing *stale*, never *healthy*; an orphan with unmerged work publishing
  *shelved*, never *reclaimed*.
- **#499** wires the new gates into the gate of record, serialized behind the
  build-file owner.
- **Vendor edge:** `kushin77/monitoring-stack#200` names the canonical
  non-Kubernetes exposition target and resolves which Prometheus survives the
  decommission; the endpoint it names becomes configuration. If the plane ever
  requires **pull** instead, that is a **new ADR** naming the plane's networking
  decision — never a quiet transport swap inside a lane.
- Recording a decision is not the same as ratifying a vendor's: if
  `monitoring-stack` or CMR later publishes a `monitoring` module or a committed
  `shared-services` manifest, the pin question returns and is answered by a new
  record, not by editing this one.
