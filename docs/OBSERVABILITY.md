# Observability — the monitoring boundary for agent-orchestrator

Status: **declared and gated** (2026-09-14). Lane: issue #496 (parent EPIC #494).
Decision of record: [`ADR-0022`](decision-records/ADR-0022-telemetry-exposition-authority-split.md)
(the telemetry-exposition authority split). Gate:
[`scripts/check-monitoring-declaration.sh`](../scripts/check-monitoring-declaration.sh).

This document is the **declaration**, not the implementation. The repo already owns
an observability *implementation*; what was missing was the *boundary* — an
explicit, gated statement of which surface this repo owns and which it does not.
It exists so that no later lane re-invents a transport, a verdict vocabulary, or a
dashboard that the fleet monitoring plane already owns.

The repo is, in one sentence, **a signal producer nobody scrapes**: the telemetry
pillar is complete and its outputs leave the process through a push exporter, and
nothing in the monitoring path writes fleet state.

## 1. The boundary — four surfaces, one direction

| Surface | Owner | What it owns |
|---|---|---|
| **Producer** | **this repo** | The *facts*: SLO kinds and verdicts, budget/quota/audit state, usage. [`telemetry/observability/`](../telemetry/observability/) is the **single source of truth (SSOT)** for the closed SLO kind + verdict vocabulary (`OK` / `AT_RISK` / `BREACHED` / `NO_DATA`), and the export renders it **verbatim**. |
| **Prometheus plane** | **`kushin77/shared-services`** | The fleet's single running observability instance (Prometheus/Grafana/Alertmanager/Loki/Jaeger). Its **`observability` capability** — flag `sharedservices.observability` — is the plane this repo targets. This repo never runs a second one. |
| **Aggregation / alerting / routing / runbooks** | **`kushin77/monitoring-stack`** | Aggregation, alert rules, severities, routes, receivers, paging, runbooks, the SLO framework and the machine dashboards. Alert names are that repo's; this repo never authors or renames them. |
| **The join node** | **the ticket** | A signal **drives a ticket**, never a silent action. The direction is one-way and permanent: **signal → ticket → human** ([`ADR-0014`](decision-records/ADR-0014-ticket-single-join-node-contract-v2.md)). Nothing in the monitoring path writes fleet state — no `fleet/**` file, no claim, no branch, no worktree is mutated by an export. |

The split is not this repo's invention: the CMR hub's committed catalog entry for
`monitoring-stack` (ratified by CMR `ADR-0042`) states that `kushin77/shared-services`
owns the fleet's single running observability instance and that `monitoring-stack`
"stands up no second runtime". This repo is the missing **consumer on the producing
end** of that boundary.

## 2. The integration identity

`module.json` declares exactly one monitoring integration, in the flat shape this
repo's own manifest already uses (mirroring the vendor catalog's integration
entries):

```json
{ "id": "prometheus", "type": "monitoring" }
```

The **capability that ties it back** is `sharedservices.observability`, owned by
`kushin77/shared-services` — *"Operational telemetry, alerting, and SLO surface for
shared platform services"*. Because this repo's integration entries carry only `id`
and `type`, that tie-back is recorded **here**, not smuggled into `module.json` as an
extra key.

**Nothing is pinned, and nothing may be invented.** Monitoring is **not** a GR-18
mandatory module: `vendor/CMR/catalog/mandatory.tsv` carries exactly three rows
(`code-indexing`, `diagrams`, `shared-frontend`), there is **no**
`vendor/CMR/catalog/modules/monitoring/` manifest, and therefore **no pinned vendor
id exists**. The entry carries no `pinned_ref`, no `module_id` and no invented
vendor id, and this program carries **no mandatory consumer asset** — declaring an
`.mcp.json`-shaped artefact for monitoring would be a false claim about a vendor
contract that does not exist.

## 3. The transport — push, not pull

**This repo pushes; the plane never has to discover or reach into it.**

- **Encoding: OTLP/HTTP** is the shipped default; **Prometheus remote-write** is the
  accepted alternate. Both render the *same* frozen content model, so switching
  encodings cannot fork a verdict, a label or a signal name.
- **There is no HTTP exposition route.** The runtime is a serverless Google Cloud
  service (Cloud Run v2) — ephemeral, scale-to-zero, no pod, no sidecar, no service
  monitor — so the vendor's default Kubernetes-annotation discovery contract cannot
  be satisfied. Push also avoids adding an unauthenticated route to a service that
  exposes none today.
- **The plane holds the ingestion config** (endpoint, credentials, retention,
  recording rules, alert rules, routes, runbooks). This repo holds only the
  rendering of its own facts and the push that carries them.
- **Enabled by default** (AO-GR-6), **inert when unconfigured** (no endpoint means no
  export, no retry storm, and never a fabricated healthy signal), endpoint and
  credential from **environment or a secret manager only** (GR-6), and a **periodic
  render** so a window that could not be evaluated is published as `NO_DATA` rather
  than vanishing.

## 4. The exposition surface (this repo's, built by #497)

The exposition surface is owned by issue **#497** — the **flag-gated-OFF push
exporter** over the telemetry feed. It renders the verdicts
[`telemetry/observability/`](../telemetry/observability/) computed **as the literal
token**, importing the closed vocabulary rather than re-declaring it, and publishes
`NO_DATA` as a first-class state (never as health). The canonical endpoint is the
**plane's** to name (`kushin77/monitoring-stack` direction issue #200) — a change of
target is a configuration change, never a code change.

The local JSONL stores remain the **local truth**; the export is a *feed* of them,
never their replacement, and a delivery failure is reported honestly (it is not a
silent success, and it is not an SLO verdict).

## 5. The two surfaces this repo does NOT own

- **The machine surface** is the monitoring plane (`shared-services` Prometheus +
  `monitoring-stack` alerting/dashboards). This repo builds no Grafana JSON, no
  `dashboards/` directory and no second Prometheus/Grafana/Alertmanager.
  ([`telemetry/observability/dashboard.py`](../telemetry/observability/dashboard.py)
  is an *offline, per-tenant* render of the producer's own data — not a fleet
  dashboard plane.)
- **The human surface** is the web single-pane-of-glass, and it is a **different
  gap** — not this one. It is analysed in
  `docs/FLEET-DASHBOARD-GAP-ANALYSIS.md` and owned by #330 → #331/#332/#333. The
  monitoring plane is the **machine** surface; the SPoG is the **human** surface;
  re-deriving either from the other is the failure this boundary exists to prevent.

## 6. How this is enforced

- The declaration is gated by
  [`scripts/check-monitoring-declaration.sh`](../scripts/check-monitoring-declaration.sh),
  which asserts the `module.json` integration entry is present and well-formed **and**
  that this document names the boundary. It **fails by name** when either is missing,
  and it **self-proves** by provoking the missing-entry and missing-marker cases in a
  scratch copy and requiring both to be refused (a check that cannot fail is a
  formality).
- Wiring this gate into the gate of record (`make verify`) is issue **#499** — the
  single wiring lane for this EPIC, serialized behind the build-file owner.

## 7. Honest limits

- This document **declares** the boundary; it does not implement the exporter (that
  is #497) or the fleet-health publisher (that is #498).
- The canonical endpoint is not named here, deliberately: it is the plane's to name.
- No pinned vendor id exists for monitoring, so none is claimed.
