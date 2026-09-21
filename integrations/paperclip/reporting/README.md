# `integrations/paperclip/reporting/` — the paperclip reporting half (issue #447)

Paperclip is the fleet's reporting agent. The orchestration half is `governance/dispatch/`
([ADR-0033](../../../docs/decision-records/ADR-0033-paperclip-hermes-naming-resolution.md));
Hermes is a flag-OFF provider reached via `governance/dispatch/route.py`
([ADR-0012](../../../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md)).
This package is the reporting duty the persona card declares as the
`module-brief` capability: it composes **each mandatory module's distributable
brief** — the organized, per-module statement of *what every repo must carry, at
which pin, and whether it is current*.

It is an extension of the canonical paperclip module
([`../README.md`](../README.md)), not a parallel one.

## The files

| File | Role |
|---|---|
| `capability.py` | the capability contract: the id the card must declare, the tools it needs (`file_read` for the three read-only sources, `file_write` to freeze the artifact), the artifact's canonical home, and the sources the brief reads |
| `composer.py` | the deterministic composition — every rendered line is a claim carrying the registry row or cited path it resolves to, plus the machine document the frozen schema describes |
| `model.py` | the claim book, citation resolution and the `BRIEF-*` refusal vocabulary; the three states and the `not-a-module` refusal are **imported** from `governance/modules/model.py` (issue #445), never restated |
| `policy.py` + `claim-policy.json` | the **declared claim-resolution policy** (issue #592): what makes a claim resolvable, what a non-resolving claim produces, and that a target-set module with no vendor `module.json` renders `target-pending` — pending is never rendered as shipped. The composer and the claim vocabulary read the artifact; neither restates it |
| `brief_schema.json` + `brief_schema.py` | the **frozen schema** of the composed brief as a machine document, and the validator the composer runs on what it emits (`BRIEF-SCHEMA-INVALID` names the JSON path) |
| `audit.py` | the **append-only audit trail**: exactly one record per composed brief run, carrying the resolved / unresolved claim counts and the finding lines. Deterministic, offline, `.verify/module-brief-audit.jsonl` by default |
| `cli.py` | `compose` / `check` / `claims` / `capability` / `audit`, tri-state (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) |
| `tests/` | the suite: the contract, the composition, the claims, the policy, the schema, the audit trail, determinism and the CLI |

## What it reads, and what it refuses

Read-only sources: the module registry
([`../../../governance/modules/README.md`](../../../governance/modules/README.md),
issue #445), the committed board snapshot (`.board/snapshot.json`) and the hub
catalog (`vendor/CMR/catalog`). The brief writes nothing but its own artifact.

```bash
python3 integrations/paperclip/reporting/cli.py compose                # the brief
python3 integrations/paperclip/reporting/cli.py check                  # artifact vs a fresh composition
python3 integrations/paperclip/reporting/cli.py capability             # declaration vs the tools it needs
python3 integrations/paperclip/reporting/cli.py audit                  # the append-only trail of composed runs
```

## The three declared artifacts (issue #592)

Each is read by the code on every run, and the gate proves it by doctoring the
artifact and requiring the refusal to change:

| Artifact | Declares | Read by |
|---|---|---|
| `claim-policy.json` | what makes a claim resolvable, what a non-resolving claim produces (a finding naming the line, never prose), and that a target-set module with no vendor `module.json` renders `target-pending` with `shipped: false` and a named blocker | `policy.load()` → `model.resolves` / `claim_findings` / `composer._module_findings` |
| `brief.schema.json` | the frozen shape of the composed brief as a machine document: per module the id, owning repo, state, pin/rev, consumer assets and the seed each comes from, health, board ref, drift — plus the claim list and the findings | `brief_schema.load()` → `composer.compose` validates the document it emitted |
| `audit.py` (trail at `<repo>/.verify/module-brief-audit.jsonl`) | one record per composed brief run: the resolved / unresolved claim counts and the finding lines, appended and never rewritten | `cli.py compose` / `check` / `claims` |

A policy the code ignored would be a decoration, so the composer holds no
second copy of any rule those artifacts state.

## Refusals are named, never silent:

| Code | Refused |
|---|---|
| `BRIEF-CAPABILITY-UNDECLARED` | the card does not declare the capability |
| `BRIEF-CAPABILITY-TOOL-UNGRANTED` | the capability needs a tool the allowlist does not grant |
| `BRIEF-CLAIM-UNRESOLVED` | a brief line cites nothing, or cites something that is not there (the line is named) |
| `BRIEF-MODULE-NO-PIN` / `BRIEF-MODULE-NO-REV` | a module that cannot be briefed |
| `BRIEF-ASSET-NO-SEED` | a consumer asset that resolves to no seed |
| `BRIEF-PENDING-RENDERED-SHIPPED` | a pending module reported as shipped |
| `BRIEF-SCHEMA-INVALID` | the emitted brief does not satisfy the frozen schema (the JSON path is named) |
| `BRIEF-STALE` | the committed artifact differs from a fresh composition |

## Live sync (issue #888)

`sync/live.py` is the surface's `live_sync` evidence
(`governance/conformance/surfaces.py` `LIVE_TOKENS`): a running module, not a
data file. `run_sync()` authenticates a caller through the boundary pipeline
(`integrations/paperclip/auth/guard.py`, issue #412), pulls **tickets** and
**budgets** through the existing HTTP seam
(`integrations.paperclip.client.PaperclipClient`) and **heartbeats** through the
existing local-beat adapter
(`integrations.paperclip.adapters.heartbeat.adapter.derive_heartbeat`),
validates the assembled record against the frozen `sync/sync.schema.json`, and
appends exactly one line to an append-only trail (default
`.verify/paperclip-live-sync-audit.jsonl`) — a record that fails validation is
refused and never written.

Refusals are named:

| Code | Refused |
|---|---|
| `SYNC-UNAUTHENTICATED` | no/invalid credential at the boundary |
| `SYNC-FORBIDDEN` | a known caller without the required permission |
| `SYNC-PAYLOAD-INVALID` | the assembled record fails `sync.schema.json` |
| `SYNC-SCHEMA-FROZEN` | the schema file is missing, unreadable, or not the frozen `$id` |

```bash
python3 -m pytest integrations/paperclip/reporting/tests/test_live_sync.py -q
```

Tests are entirely offline: tickets/budgets replay through
`client.FixtureTransport` against `integrations/paperclip/tests/fixtures/api.json`,
heartbeats replay through a canned reader, and the signing key is an
obviously-fake placeholder — no test here calls a live network.

## Distribution

The brief, not a copy, travels with the assets: distribution stays with the
hub's existing channel (`controller/standards-sync.sh`,
`controller/standards-manifest.txt`, `templates/module/` seeds). This package
adds **no** second push mechanism and vendors nothing (GR-10 / ADR-0013 / NG4).

## Tests and gate

```bash
python3 -m pytest integrations/paperclip/reporting -q
bash scripts/check-module-brief.sh
```
