---
id: ADR-0015
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0015: Routing seam — the extracted tier-routing package is a policy source we map, not a runtime we couple

## Status

`accepted` — ratified on the PR for issue #426 (child of EPIC #422, milestone
M28). It applies to the peer artifact `kushin77/hermes-agents#2` the rule
[`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) already fixed for this class
of decision — *map the policy, do not couple the runtime* — and freezes the seam
as the machine-readable contract under
[`../contracts/routing/`](../contracts/routing/README.md). It supersedes no
earlier decision, and ADR-0012 stands unamended. The decision is taken inside the
cross-repo boundary [`../CROSS-REPO-EXECUTION-BOUNDARY.md`](../CROSS-REPO-EXECUTION-BOUNDARY.md)
(NG4): the peer's package is addressed on **their** board, never by an edit or a
fork here.

## Context

This repo holds, and now declares, a **single routing authority**, and a peer is
about to extract a second engine with the same purpose. The decision must be made
before the second engine exists, because the failure mode is not a runtime crash
— it is two engines quietly both being authoritative.

**1. The local authority is real and has an engine.** `gateway/sme-routing/`
(issue #149) is the routing authority: an offline, deterministic engine
(`router.py`) that reads declared policies under `gateway/sme-routing/policies/`
(route-policy, tier-policy, capability-registry), validates them with semantic
invariants, and answers `route(task)` / `dispatch(task)` with a **route**, an
**agent chain**, a **model tier** and that tier's **cost controls**. It carries a
tri-state CLI (0 / 1 / 2), mutation controls in its own suite, and a fail-safe
(`dispatch_defaults.unknown_task_type` is enforced to be the deep path). It is
not a placeholder.

**2. The adjacent FinOps vocabulary is also local.** `gateway/finops/chooser.py`
and `gateway/finops/complexity.py` already own the tier chooser and the
0-100 difficulty scorer. `gateway/finops/complexity.py`'s own docstring records
that it is *adapted from the hermes-agents `services/complexity_scorer.py`* —
which is the point of fact §5 below.

**3. The peer artifact, measured (2026-09-14).** `kushin77/hermes-agents#2`,
`[assimilation] Extract tier-routing brain as a standalone reusable package`, is
**open** on the peer's board and declares itself an extraction of four pure-stdlib
modules — `services/complexity_scorer.py`, `models/model_tiering.py`,
`services/escalation_handler.py`, `services/capability_registry.py` — to be
*"packaged as an installable module so shared-governance orchestrator/ imports
them"*, in service of the peer's own **Agent College** program
(`shared-governance#412`, slices S3/S4/S6). There is **no release and no
published package**: nothing under `vendor/` ships it, nothing in this repo
imports it, and the string `hermes-agents#2` appears in this repo nowhere outside
this ADR's own lane.

**4. This is the same surface, twice.** The four modules the peer will extract
name exactly the mechanisms `gateway/sme-routing/` and `gateway/finops/` already
implement here: a complexity scorer, a model-tier / escalation config, an
escalation handler, and a task-type→agent capability registry. ADR-0012 recorded
the same overlap one layer up (the hermes persona card's provenance already cites
`hermes-agents` `services/capability_registry.py` and `models/model_tiering.py`).
A second engine over the same surface is the outcome ADR-0012 named **worst**:
**half-coupling** — two authoritative routing engines, which is what makes a later
migration unrunnable.

**5. The "map its policy" relationship is already the house practice.** This repo
already consumes hermes-agents as a **pattern/policy source** under GR-10
provenance and never as a runtime: `gateway/finops/complexity.py` is adapted from
the peer's `complexity_scorer.py`, and the SME-routing policies under
`gateway/sme-routing/policies/` are provenance-stamped ports of declared
configuration, not imports. ADR-0012's accepted path — a *contract* and a
*pattern source*, not a service — is therefore not new policy; it is the practice
this repo already follows, which this record makes explicit for the new artifact.

**6. Decision space.** Three answers are live: consume the extracted package as a
**library** we import at run time; treat it as a **policy source we map** into our
own declared policy; or **not adopt** it at all.

## Decision

**The extracted tier-routing package is a _policy source we map_, never a runtime
we couple.** `gateway/sme-routing/` remains the **single routing authority** in
this repo: it produces every decision field and owns every policy field, and the
peer package produces no field of the routing contract. The peer artifact is
recorded as a **source** — never as a **producer** — in the seam's ownership map
([`../contracts/routing/ownership.json`](../contracts/routing/ownership.json)).

The decision is decided by ADR-0012's **three-fact test**, applied to this
artifact:

| Fact | Question | Answer here |
|---|---|---|
| 1. **Availability** | Is the extracted package deployable, released, importable, or running? | **No.** The extraction is *planned* (`hermes-agents#2` is open, unlabelled, no release); nothing under `vendor/` ships it and nothing here imports it. It is an artifact that does not yet exist as a consumable. |
| 2. **Dependency** | May our control loop depend on it at run time — its availability, latency, failure modes, and its own release cadence — for routing? | **No.** Routing is the most latency- and availability-sensitive path in the control plane, and the peer extraction is built for a *different* consumer (`shared-governance` Agent College). Depending on it would put a peer's package on our critical path and create the half-coupling ADR-0012 named worst. |
| 3. **Ownership** | Is it a product to dogfood, or a pattern/policy source? | **A policy source.** Its patterns inform our declared policy, recorded with GR-10 provenance; the declared policy itself is owned here, by `gateway/sme-routing/`. |

### Chosen — the package is a **policy source we map**

We read the peer's tier-routing patterns as input to our own declared policy, the
way `gateway/finops/complexity.py` already maps its `complexity_scorer.py`. The
mapping is one-directional and non-binding: it informs `gateway/sme-routing/policies/`,
and the resulting declared policy is ours, under our provenance ledger and our
gate. To keep the seam cheap to swap, dispatch asks the `RoutingPolicy` port for a
decision (ADR-0012); if this decision is ever reversed, the fleet replaces one
implementation of that port rather than rewriting the contract.

**Consequence (chosen).** The fleet keeps one authoritative engine, so a migration
later swaps an implementation, not a contract. The peer's work is still available
to us as a pattern source under GR-10, so a good idea does not have to be
re-derived. The cost: our routing vocabulary is bound to the frozen seam — a
divergence is now a finding, not a refactor — and a genuine gap in the peer's
patterns becomes a direction issue to their board (NG4), not a local edit.

### Rejected, explicitly — we do **not** consume the runtime

**Consuming the runtime is rejected explicitly.** This option would import the
extracted package (or call it as a service) so that dispatch resolves tier /
escalation through the peer's code.

**Consequence (rejected).** It buys nothing we do not already have — the same
surface is implemented and declared here — while it costs a run-time dependency on
a planned, unreleased, third-party artifact, on the control plane's most
availability-sensitive path. Worst of all it creates **half-coupling**: two
authoritative routing engines, where a task's tier could be decided by either. ADR-0012
names that the outcome that makes a migration unrunnable, and it is exactly the
seam this record exists to prevent. Rejecting it is the load-bearing part of this
decision.

### Rejected — **not adopted**

We do not ignore the artifact either. Declining to adopt it outright would leave
the overlap undecided — the same accretion ADR-0012 had to clean up — and would
forfeit the pattern source the repo already uses. Not adopting is a decision to
keep re-deriving a contract a peer is publishing; mapping it is strictly better
and costs no run-time coupling.

**Consequence (rejected).** No run-time coupling and no second authority either
way; the difference is whether the peer's patterns are available to our declared
policy under provenance. Mapping keeps them available; not adopting does not.
Nothing is foreclosed by rejecting this option, and a later reversal is a new ADR.

## Consequences

- **Positive:** the routing seam now has one declared authority and one frozen
  contract, so the second engine cannot arrive as an unowned accretion. Half-coupling
  is refused **by name** at the gate ([`../contracts/routing/`](../contracts/routing/README.md),
  `scripts/check-routing-seam.sh`): a field with two producers and a field with no
  producer each fail. The peer artifact is usable as a pattern source without ever
  becoming a runtime dependency, and the existing house practice — map the policy,
  never couple the runtime — is now stated rather than assumed.
- **Negative:** a new normative contract and gate must be kept true as the router
  evolves; the routing vocabulary is no longer free to drift locally. The peer
  package is a read-only source, so a gap in it cannot be fixed here — it becomes a
  direction issue to `kushin77/hermes-agents` under NG4.
- **Neutral:** no code changes. This record writes **no file under
  `gateway/sme-routing/`**; the router is the authority and any change to it is a
  separate issue on that lane. The provider adapters and `gateway/finops/` are
  untouched. The ownership map, not the router, is where the seam is expressed.
