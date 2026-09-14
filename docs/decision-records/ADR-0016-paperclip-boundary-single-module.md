---
id: ADR-0016
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0016: One home for the paperclip boundary adapter — consolidate the parity adapters under `integrations/paperclip/`

## Status

`accepted` — ratified on the PR for issue #457 (milestone M27, "Paperclip boundary
& parity adoption"). It fixes the relationship between the two modules that
implemented the same fleet↔paperclip.ing boundary and executes the consolidation
it mandates. It supersedes no earlier decision: the boundary it operates within is
[`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (*map the policy, do not couple
the runtime*) and the integration mode is
[`ADR-0013`](ADR-0013-paperclip-ing-integration.md) (adopt the upstream CLI over
HTTP). The ownership convention it applies is
[`ADR-0010`](ADR-0010-canonical-copy-ownership.md) (canonical-copy ownership).

## Context

Two top-level modules implemented the same fleet↔paperclip.ing boundary, and the
guard that was meant to keep them apart reported the second one as a pass.

**The two modules, measured 2026-09-14 (this lane, `origin/master` at the lane's
base).**

- `integrations/paperclip/**` — **canonical.** `client.py` (the `Transport`
  protocol, `HttpTransport` and `FixtureTransport`), `mapping.py` (the
  deterministic mapper), `model.py`, `cli.py` (`plan`/`check`/`push`), `budget.py`
  (the §5.7–§5.10 budget rail), `tests/`, `README.md`. Landed by issue #428
  (PR #433, commit `9cd5794`) under ADR-0013, extended by #347 and #415. It is
  consumed cross-tree — `telemetry/audit/tests/test_paperclip_activity.py` imports
  `integrations.paperclip.mapping`.
- `paperclip/adapters/{approvals,heartbeat,secrets,skills}/**` — 57 files, the
  EPIC #410 parity adapters, landed by #416 (PR #438), #417 (#439), #419 (#441)
  and #414 (#451). Self-declared in `paperclip/__init__.py` as *"Paperclip-ing
  parity adapters (EPIC #410) … projection, not authority … Families: approvals,
  heartbeat, budget, secrets, routines, skills."*
- **Neither tree imports the other.** `grep -rn "paperclip\.adapters"
  integrations/paperclip` and the reverse are both empty: the two modules are
  independent implementations over the same frozen seams in
  `docs/contracts/paperclip/`. That is exactly the **half-coupling** ADR-0012
  names the worst outcome — two implementations for one boundary, either of which
  could be taken as authoritative.

**The smoking gun.** The *budget* family (§5.7–§5.10) landed in the **canonical**
module (`integrations/paperclip/budget.py`, issue #415) while the other four
families landed under `paperclip/adapters/`. The same family type therefore has
two homes — the boundary's families are not even internally consistent about which
module owns them.

**How the second tree happened.** The lane briefs for #412/#413/#414–#419/#447
were written *before* #428 landed and named paths that never existed —
`paperclip/auth/**`, `paperclip/api/**`, `paperclip/adapters/<name>/**`,
`paperclip/reporting/**`. Four lanes followed the brief literally and built a
second tree. Issue #448 corrected the briefs and landed
`scripts/check-paperclip-canonical-module.sh`, but that guard **reported** the
resulting `paperclip/` tree as a `KNOWN` sibling — a pass — rather than failing
it. A guard that names a duplicate and then accepts it is the false-green this
repo's doctrine forbids (AO-GR-12): the invariant "one module" was written down
but not enforced.

**The decision space.** Two answers are live:

1. **Layered** — keep two modules and assign layers: `integrations/paperclip/` is
   transport + canonical mapper; `paperclip/adapters/` holds per-family
   projections *over* it, with an asserted one-way dependency.
2. **Consolidation** — one module: the parity adapters become subpackages of
   `integrations/paperclip/`, and `paperclip/` ceases to exist.

## Decision

**One home.** `integrations/paperclip/` is the **sole** module for the paperclip
boundary adapter. The per-family parity adapters live at
`integrations/paperclip/adapters/<family>/`; the top-level `paperclip/` tree is
**deleted**. There is exactly one place a concept of this boundary may be
implemented, and the guard refuses any second one **by name**.

The module's layout, after this record:

| Path | Role |
|---|---|
| `integrations/paperclip/{client,mapping,model,cli}.py` | the seam — transport, deterministic mapper, `plan`/`check`/`push` |
| `integrations/paperclip/budget.py` | the budget rail — a cross-cutting projection the mapper emits and this validates (§5.7–§5.10) |
| `integrations/paperclip/adapters/<family>/` | the per-family projections — `approvals`, `heartbeat`, `secrets`, `skills` |

**Dependency direction (stated explicitly, whichever option was chosen).**
`adapters/**` **may** import the seam
(`integrations.paperclip.{client,mapping,model,budget}`); the seam and its root
**must never** import `adapters/**`. The seam is the base layer; the family
adapters are projections layered over it, one direction only. A future import
from the root into a family subpackage is the second home re-growing, and is a
finding.

### Chosen — consolidation

Consolidation yields the **fewest surviving homes** for one concept: one, not two.
It is also the option the surrounding artifacts already assume — every still-open
sibling lane brief and the guard's own canonical-home message point at
`integrations/paperclip/`. The four family adapters remain a coherent tree (their
own schemas, tests and plugins) as one subpackage, so nothing is scattered; and
the budget-family smoking gun disappears, because every family now lives under one
module. Finally, it makes the guard's invariant **literally true** — there is one
module — so the guard can refuse *any* second `paperclip/` module by name instead
of keeping an escape hatch for a "declared" one.

### Rejected — layered

Layered is rejected explicitly. It preserves **two homes** for one concept, which
is the cost this record exists to remove: the guard must keep a declared-sibling
escape hatch (the `KNOWN`-and-pass behaviour #448 shipped), and the ambiguity
persists for every future lane. It also leaves the boundary internally
inconsistent — the budget family at `integrations/paperclip/budget.py` while its
four siblings sit in `paperclip/adapters/`. And it would **assert a dependency
that does not exist today**: the two trees import neither each other
(measured above), so "adapters over the seam" would be a rule with nothing
exercising it — decoration, not a guard. Keeping a sibling also directly
contradicts every still-open lane brief, which points at `integrations/paperclip/`.

**Consequence (rejected).** With layered, the `KNOWN` acceptance stays, so a
second module can still land green; the half-coupling ADR-0012 names worst would
survive a decision record purporting to fix it. Nothing else is foreclosed — a
later move from consolidated back to layered is a **new** ADR, not an edit of this
one.

## Consequences

- **Positive:** one home for the paperclip boundary; the duplicate is refused
  **by name**, not reported as `KNOWN`, so the invariant is enforced rather than
  documented. The guard is now *strictly stronger* than the one it replaces: the
  former declared sibling — a `paperclip/` module naming EPIC #410 — is itself a
  failure. Cross-tree consumers are unaffected (`telemetry/audit/` already imports
  the canonical mapper, never the sibling).
- **Negative:** 57 files relocate, and every cross-reference is rewritten —
  `paperclip.adapters.*` module paths, `paperclip/adapters/...` string paths, and
  the `Path(__file__).resolve().parents[N]` repo-root derivations (each moved file
  is one directory deeper). The four family gates
  (`scripts/check-paperclip-{approvals,heartbeat,secrets,skills}.sh`) are
  re-pointed. Because same-named test modules (`test_cli.py` under secrets and
  skills; `test_schema.py` under the seam and secrets) would otherwise collide
  once they share one package root, the moved `tests/` directories carry an
  `__init__.py` package marker. The `paperclip.adapters.*` import path is retired:
  a reversal is a **new** ADR.
- **Neutral:** the budget rail stays at `integrations/paperclip/budget.py` (it is a
  seam-level projection the mapper emits, not a per-family adapter). The seam files
  (`client.py`, `mapping.py`, `model.py`, `cli.py`) are untouched.

**Follow-ups.** The four family gates remain **unwired** into `make verify` — this
record does not touch the build files; wiring is issue #420's lane. Recorded, not
done here.
