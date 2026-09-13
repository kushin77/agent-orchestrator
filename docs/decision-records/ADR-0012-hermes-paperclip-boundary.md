---
id: ADR-0012
status: accepted
date: 2026-09-13
deciders: [owner]
req: []
supersedes: []
---

# ADR-0012: Hermes/Paperclip ownership boundary for the fleet

## Status

`accepted` — ratified by issue #299 (EPIC: Fleet ↔ Hermes/Paperclip alignment
roadmap, milestone M26), which carries the roadmap this record governs. It
supersedes no earlier decision and is superseded by none. The decision is
reproduced as the EPIC's governing section so the roadmap and the record cannot
drift apart.

## Context

This repo vendors two CMR modules whose declared purpose is, verbatim, what this
repo's own fleet layer already does. The overlap was never decided — layer by
layer it accreted, and the harvest index recorded the gateway and registry
layers (see [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md) §10 and §11) while
never deciding who owns the **fleet dispatch** layer. That gap is what forces a
decision now.

**1. The vendored owner already declares this surface.** The module
`vendor/CMR/catalog/modules/hermes-agents/module.json` describes itself as
*"Agent routing, capability registry, escalation, and model-tiering patterns for
multi-tenant agent orchestration and persona-driven task execution"*, with four
features that name the mechanisms outright:

| Feature (declared) | Declared description |
|---|---|
| `capability-registry` | "Capability registry and agent persona metadata used to resolve the correct agent for a task." |
| `task-routing` | "Deterministic task-to-agent and task-to-model selection using capability and risk metadata." |
| `model-tiering` | "Complexity and escalation rules that map work to default or safer model tiers." |
| `escalation` | "Escalation behavior for uncertainty, timeout, confidence thresholds, and human-in-the-loop handoff." |

**2. The fleet hand-implements exactly that.** [`../../fleet/brain.py`](../../fleet/brain.py)
derives the FinOps block (`choose_model`), escalates a security / secrets / auth
/ IaC lane to the high floor (`needs_high_floor`), and dispatches — with the
vocabulary read from [`../../fleet/profiles/brain.profile.json`](../../fleet/profiles/brain.profile.json)
so the doctrine and the machine cannot drift. [`../../governance/dispatch/`](../../governance/dispatch/README.md)
adds the dispatch bookkeeping: claims, the milestone frontier, dependency-ordered
eligibility. Agent selection, capability resolution, tiering and escalation are
all present. **We are re-implementing Hermes**, and no decision record says so.

**3. The second module owns the reporting half.** `vendor/CMR/catalog/modules/paperclip/module.json`
declares *"Planning and reporting workflow patterns for roadmap tracking, project
health, and status-report discipline across governed workstreams"*, with
`roadmaps` ("plan-first artifacts and roadmap tracking for workstreams and
milestones"), `status-reports` ("project status and delivery reporting patterns
with crisp, human-readable output") and `skills-and-governance`. The fleet's
reporting surfaces — the console dashboard, the wave plans, the status and
health lines — produce precisely those artifacts, as an unowned accretion rather
than against a stated pattern.

**4. The personas are real, registered, and already dispatched.** Which makes the
boundary operational rather than theoretical:

| Persona card | Role | Tier | Lanes | Capabilities |
|---|---|---|---|---|
| [`../../registry/personas/cards/hermes.yaml`](../../registry/personas/cards/hermes.yaml) | coding worker | **MED** | `hermes`, `code-authoring` | `code-author`, `test-author`, `test-run`, `memory-ops` |
| [`../../registry/personas/cards/paperclip.yaml`](../../registry/personas/cards/paperclip.yaml) | knowledge assistant | **LOW** | `paperclip`, `knowledge` | `research`, `docs-authoring`, `memory-ops` |

They are registered by `registry/profiles/seeds/hermes.1.0.0.yaml` and
`paperclip.1.0.0.yaml`, bundled in
`registry/packs/releases/purebliss-team.1.0.0.yaml`, and the hermes card's own
provenance already cites `kushin77/hermes-agents`
`src/hermes_agent/services/capability_registry.py` and `models/model_tiering.py`.
The contract is being consumed by the registry; it was simply never declared
for the fleet.

**5. Two layers that must not be conflated.** `gateway/providers/hermes.py` and
`gateway/providers/paperclip.py` are **inference-only** provider adapters — an
Ollama-compatible `/api/chat` shape and an OpenAI-compatible `chat/completions`
shape respectively. They route no work and decide no tier. Provider selection
(`agent → provider`, frozen in `routingGroups.purebliss-team`) is the gateway's
job; **capability routing** (`work → agent`) is the brain's. This decision is
about the second one only.

**6. Decision space.** Three options were live: keep growing the hand-rolled
implementation and declare no owner; adopt the vendored module as the declared
owner of the layer and consume its contract explicitly; or copy its source into
the fleet and own a fork. Prior art sits in
[`ADR-0011`](ADR-0011-session-fleet-transport.md), which settled a comparable
boundary by naming *which artifact is authoritative for what*, so that a later
migration swaps an implementation rather than rewriting a contract — the same
shape is applied here.

## Decision

**The orchestration layer has a declared owner: the vendored `hermes-agents`
module.** The fleet adopts that module's routing / capability-registry /
escalation / model-tiering contract as the vocabulary of record for dispatch,
cannibalizes its patterns under **GR-10** provenance into `fleet/` and
`governance/dispatch/`, and **never edits `vendor/`** — the submodule is a
pinned read-only source (GR-5). `paperclip` owns the **reporting** half of the
same boundary — planning, roadmap tracking and status-report discipline — not
the routing half.

**(a) Ownership is declared, not implied.** `hermes-agents` is the declared
owner of the orchestration layer. The fleet's `choose_model` / tier-floor /
escalation / dispatch behaviour is a **consumption** of that contract — recorded
with GR-10 provenance in `docs/CANNIBALIZATION.md` (GR-10) — never a competing
implementation grown independently.

**(b) Work is routed by capability from the registry personas.** Dispatch selects
the worker by the capability the work needs, read from the registry personas that
are actually dispatched: code / test / PR authoring routes to the `hermes`
persona at **MED** (`code-author`, `test-author`, `test-run`); research / docs /
reporting routes to the `paperclip` persona at **LOW** (`research`,
`docs-authoring`). Tier escalation remains a **floor, never a ceiling**: the
high-floor rule — security, secrets, auth and production-IaC work never start
below HIGH — survives this decision unchanged and outranks the persona tier.

**(c) The reporting surface aligns with paperclip.** The console dashboard, the
wave plans and the status reporting adopt paperclip's planning / roadmap-tracking
/ status-report discipline as their pattern source, so the reporting half of the
fleet has one declared owner too.

**(d) Migration is a non-blocking adapter — the fleet keeps running.** No
stop-and-swap, no big-bang rewrite, no freeze: the alignment lands as an adapter
over behaviour that is already live. A change that cannot land without stopping
the fleet is out of scope for this decision.

**Out of scope, stated so it is not conflated:** the gateway adapter modules
named in Context §5 are inference-only and unchanged by this decision. This
record decides the *ownership boundary and the routing contract*; it edits no
code, and it does not move provider routing.

## Consequences

- **Positive:** the question "who owns agent routing here?" now has a recorded
  answer, so the fleet stops paying to re-derive a contract it already vendors.
  Dispatch becomes capability-routed from the registry personas, which makes the
  registry load-bearing instead of decorative. The reporting surface acquires an
  owner as well, turning status discipline into a pattern to meet. The migration
  is non-blocking, so the live loop is never interrupted for it. GR-10 provenance
  for the dispatch layer is recorded, closing the gap that §10 and §11 of
  [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md) left open.
- **Negative:** the fleet's dispatch vocabulary is no longer free to drift
  locally — a divergence from the declared contract is now a finding rather than
  a refactor. The dependency is read-only, so a genuine gap in the vendored
  contract cannot be fixed in this repo; it becomes a direction issue to the hub.
  Parts of the roadmap serialize on shared files, and the dispatch-by-capability
  child waits on the open issue that already owns `fleet/brain.py` in this wave.
  A new normative document must be kept true as the fleet changes.
- **Neutral:** nothing is renamed, no transport changes
  ([`ADR-0011`](ADR-0011-session-fleet-transport.md) stands unchanged), no data
  migrates, and the gateway adapters are untouched by explicit scope. The
  personas and their tiers already exist; this decision says they are the routing
  contract, rather than introducing them.

**Follow-ups.**

- #300 — dispatch by capability: the brain consults the registry personas.
- #301 — cannibalize hermes-agents routing / tiering / escalation into fleet
  dispatch, with the GR-10 provenance record.
- #302 — cannibalize paperclip planning / status-report patterns into the fleet
  reporting surface.
- The index in [`README.md`](README.md) should list this record. It is recorded
  here as a follow-up because this change is deliberately scoped to this file
  alone; the index row is the one thing deliberately left un-R'd.
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) may want a pointer to this record
  where it describes the dispatch and reporting layers.

## Alternatives

1. **Declare no owner; keep growing the hand-rolled implementation.** Rejected.
   The overlap is verbatim (Context §1–§2), and the ambiguity is precisely what
   produced this record: undecided ownership does not avoid the coupling, it just
   hides it while the fleet re-derives a vendored contract.
2. **Give one module both halves — fold reporting into `hermes-agents`.** Rejected.
   `paperclip` already declares the planning / status-report surface, and the
   vendored catalog deliberately draws that line. One module owning both would put
   status-report discipline inside a routing module and erase a separation the
   upstream already made.
3. **Copy the hermes-agents source into the fleet and own a fork.** Rejected.
   Copying a pinned vendored source duplicates it (GR-10 `harvested_from`
   markers for no benefit), creates a divergence surface with no upstream to
   reconcile against, and forgoes the one thing that is actually useful here —
   the pattern and the vocabulary, not the file.
4. **Move dispatch into the gateway; make the gateway the router.** Rejected.
   It conflates `work → agent` (capability routing, a control-plane decision)
   with `agent → provider` (transport selection, already frozen in
   `routingGroups.purebliss-team`). A gateway that chose *which worker* runs a
   task would put a control-plane decision inside the transport tier.
5. **Stop the loop and swap the fleet onto the hermes-agents service.** Rejected.
   The module is a pattern source in this repo, not a deployed dependency, and a
   stop-and-swap buys no capability the adapter path does not — while violating
   decision (d) outright.

## Migration

A **non-blocking adapter over live behaviour**, in dependency order. No phase
requires the fleet to stop, and no phase changes data at rest or the transport.

| Phase | Work | Behavioural risk |
|---|---|---|
| **0 — this record** | The decision plus roadmap (#299 with #300, #301, #302). | None. No code changes; the loop keeps running. |
| **1 — #300** | Capability → persona resolution in the brain, from the registry cards; high floor preserved. | A dispatch decision changes; a test pins both routes and the escalation floor. |
| **2 — #301** | The `governance/dispatch/` chain aligned to the adopted contract, plus the GR-10 provenance record (the single writer of `docs/CANNIBALIZATION.md`). | Internal to dispatch bookkeeping; the claim chain and its gate are the safety net. |
| **3 — #302** | The reporting surface aligned to paperclip's pattern; read-only with respect to dispatch. | Reported output changes; no dispatch decision can move. |

**Rollback.** Each phase is an adapter over behaviour that is already live, so
reverting is the revert of one PR. Phase 0 carries no behaviour to roll back, and
phases 1–3 leave the transport, the mailbox and the claim chain untouched
([`ADR-0011`](ADR-0011-session-fleet-transport.md) stands).

**What would require a new record.** If the fleet ever needs routing behaviour
this contract does not express — per-tenant routing policy, for instance, which a
multi-tenant control plane will eventually want — that is a **new** ADR, not an
edit to this one.
