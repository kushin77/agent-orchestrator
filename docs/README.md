# Docs — agent-orchestrator

Index of the repo's canonical documentation. Agents start at
[`../AGENTS.md`](../AGENTS.md) (precedence-ordered doctrine).

## Canonical docs

| Doc | Purpose |
|-----|---------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Five-pillar control-plane architecture (source of truth; EPIC-00 = issue #4). |
| [`EXECUTION-PLAN.md`](EXECUTION-PLAN.md) | One-issue-one-lane parallel dispatch contract, phase/wave sequencing 0–8. |
| [`PAPERCLIP-ING-GAP-ANALYSIS.md`](PAPERCLIP-ING-GAP-ANALYSIS.md) | Sourced fork-map of upstream `paperclip.ing` against the fleet's own primitives (issue #368). |
| [`GOVERNANCE.md`](GOVERNANCE.md) | Branch, provenance, session-label, review/merge conventions. |
| [`PAPERCLIP-ING-INTEGRATION.md`](PAPERCLIP-ING-INTEGRATION.md) | Frozen fleet ↔ upstream Paperclip integration seam — heartbeat/ticket/budget contracts (ADR-0013, issue #370). |
| [`CROSS-REFERENCE-SPINE.md`](CROSS-REFERENCE-SPINE.md) | Typed relationship edges (`cmr-refs:` markers, closed vocabulary, RCA nodes) — EPIC #138, issue #384. |
| [`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) | The cross-repo boundary contract — a repo remediates findings for itself only; foreign work is handed over by direction issue (NG4, issue #125). |
| [`CTO-OVERLAY.md`](CTO-OVERLAY.md) | The per-repo drop-in governance overlay — four real layer gates, BLOCKING/WARNING, preserved no-false-green tally (EPIC #144, issue #147). |
| [`AUTHORITY-MODEL.md`](AUTHORITY-MODEL.md) | Scoped admin rights, repo separation, schema-enforced separation of duties, end-to-end closure (EPIC #144, issue #150). |
| [`ENTERPRISE-ROLLUP.md`](ENTERPRISE-ROLLUP.md) | Tenant hierarchy + org aggregate view — a projection over per-repo fleets, never a second source of truth (EPIC #144, issue #151). |
| [`SESSION-FLEET-LESSONS.md`](SESSION-FLEET-LESSONS.md) | Session-fleet lessons register: six lessons from first live steering + seven-item enterprise hardening register, each item mapped to its owning issue (issue #180). |
| [`FLEET-TEMPLATE.md`](FLEET-TEMPLATE.md) | The parameterized per-repo fleet template — schema-validated composition, definition-vs-run-state split, drift + two-repo isolation (EPIC #144, issue #146). |
| [`SME-ROUTING.md`](SME-ROUTING.md) | SME-squad routing + capability/route/tier FinOps: domain→SME, complexity→chain+tier, caps and the human/advisor escalation terminal (EPIC #144, issue #149). |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Human contributor workflow. |
| [`../RELEASING.md`](../RELEASING.md) | SemVer release process. |

## Planned (later issues)

- `CANNIBALIZATION.md` — index of harvested/cannibalized assets (issue #8).
- `adr/` — architecture decision records (issue #7).
