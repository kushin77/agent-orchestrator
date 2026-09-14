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
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | Human contributor workflow. |
| [`../RELEASING.md`](../RELEASING.md) | SemVer release process. |

## Planned (later issues)

- `CANNIBALIZATION.md` — index of harvested/cannibalized assets (issue #8).
- `adr/` — architecture decision records (issue #7).
