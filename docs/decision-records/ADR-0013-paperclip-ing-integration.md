---
id: ADR-0013
status: accepted
date: 2026-09-13
deciders: [owner]
req: []
supersedes: []
---

# ADR-0013: Adopt the upstream Paperclip CLI as the operator surface (embed-vs-fork-vs-CLI)

## Status

`accepted` — ratified on the PR for issue #370 (EPIC: Fleet ↔ Hermes/Paperclip
alignment roadmap, milestone M26). The decision reproduced here is the same one
the normative seam doc [`../PAPERCLIP-ING-INTEGRATION.md`](../PAPERCLIP-ING-INTEGRATION.md)
freezes as machine-readable contracts, so the record and the seam cannot drift
apart. It supersedes no earlier decision; the ownership boundary it operates
within is [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md), which stands
unamended.

## Context

The fleet now answers a workspace-level question: **what is the operator surface
for `ai.purebliss.app`?** Today the operator surface is hand-built — the fleet TUI
(`fleet/console.py`), the control verbs (`fleet/control.py` `pause` / `resume` /
`kill`), the claim ledger (`governance/dispatch/`), the budget rail
(`telemetry/budgets/`, `gateway/finops/`) and the audit stream (`.fleet/slog.jsonl`,
`telemetry/ledger/`). Upstream `paperclipai` is an MIT, self-hosted product with a
real HTTP API (`/api`, dev base `http://localhost:3100/api`, `GET /api/health`,
`GET /api/openapi.json`, company-scoped routes `/api/companies/{companyId}/...`,
`Authorization: Bearer <agent-key-or-jwt>`, session cookie or board token, and an
`X-Paperclip-Run-Id` carried on a mutating request made during an agent run). It
also ships its own UI and a CLI: `npx paperclipai onboard --yes`.

**The decision space.** Three integration modes were live, and each buys a
different failure mode:

1. **Embed** the upstream UI inside our portal (`portal/`). Cheap to start,
   maximum coupling: two identity systems (our tenants/RBAC vs upstream's
   companies/agents), two session models, a UI we do not own rendering on top of
   a runtime we do not own. The identity-duplication cost is paid forever, and
   every upstream UI release is a front-end regression we must absorb.
2. **Fork** upstream and carry our own UI changes. We own the code, but we also
   own the entire upgrade cost: every upstream release becomes a rebase, and the
   divergence grows monotonically — the classic fork trap, worst exactly when the
   upstream is moving fastest.
3. **Adopt the CLI** (`npx paperclipai`) as an **external operator surface** driven
   over its HTTP API. This is a **process boundary, not a fork and not an embed**:
   upstream keeps its UI and its runtime, we keep ours, and the two meet at an
   explicit, versioned API seam. The cost is real — a process boundary and
   cross-boundary auth integration — but it is *bounded and one-time*, not
   recurring like a fork's rebase or an embed's identity duplication.

**What forces the decision.** Three facts, already established:

- **Upstream is MIT and self-hosted.** The license permits all three modes; the
  choice is therefore architectural, not legal. Self-hosting means we can run it
  beside our control plane without a vendor lock.
- **We already own a control plane.** `fleet/` and `governance/` own dispatch,
  claims, budgets and audit, and ADR-0012 fixed the operating rule: **map the
  policy, do not couple the runtime** — no two authoritative engines. Embedding or
  forking would create a second authoritative runtime for exactly the surfaces we
  already own, violating that rule; adopting the CLI keeps the two runtimes on
  opposite sides of a process boundary with a single declared seam.
- **The upstream contracts are already shaped like ours.** Its heartbeats (event
  wakes + scheduled ticks, delta-carrying, outcome-or-named-blocker), tickets (one
  accountable owner, in-progress / blocked / in-review / done, explicit blocked-by,
  goal fan-out, append-only audit) and budgets (per-agent cap, hard stop with a
  graceful hand-off, top-up as approval, burn-rate alerts, per-task receipts) map
  cleanly onto fleet shapes — measured, with the mismatches named, in the seam doc.
  A clean mapping is what makes a *seam* viable; it is not what makes embed or fork
  viable, because those fail on ownership, not on shape.

## Decision

**The integration mode is: adopt the upstream Paperclip CLI (`npx paperclipai`) as
an external operator surface, and integrate over its HTTP API.** We do not embed
its UI into `portal/`, and we do not fork it. Upstream runs as its own process; the
fleet integrates across a **process boundary** through the three contracts frozen
in [`../PAPERCLIP-ING-INTEGRATION.md`](../PAPERCLIP-ING-INTEGRATION.md) — heartbeat,
ticket and budget — with cross-boundary auth carried by the upstream `Authorization`
/ session-cookie model and the `X-Paperclip-Run-Id` run correlation header.

**Why adoption over embed or fork.** Embedding pays an unbounded identity-duplication
cost (two tenant/RBAC models, two session models) to render a UI we would still not
own; forking pays a monotonically growing rebase cost and guarantees divergence from
a moving upstream. Adoption pays a **bounded, one-time** cost — stand the process up,
agree the seam, integrate auth once — and thereafter every upstream release lands on
their side of the boundary without touching our tree. When the recurring cost of a
fork or the permanent coupling of an embed is weighed against a one-time boundary
cost, adoption is the only mode whose cost does not grow over time.

**This is the same shape ADR-0012 used.** ADR-0012 decided *map the policy, do not
couple the runtime* for the routing half; this record applies it to the operator
surface: we map the heartbeat / ticket / budget contracts and do **not** couple the
runtime. Upstream is not a second authoritative engine for our fleet — the seam is
an adapter over the live loop, and the fleet keeps running through it.

**What stays OURS regardless of mode** (out of the integration's scope by
construction, not by aspiration):

- the **terminal TUI** `fleet/console.py` — the operator's console stays ours;
- the **claim ledger** `governance/dispatch/` — claims, the milestone frontier and
  dependency-ordered eligibility remain authoritative here;
- the **FinOps tiers** — `governance/finops/`, `gateway/finops/` and the
  `telemetry/budgets/` rail remain authoritative here.

**Explicitly out of scope.** This record decides the integration mode and freezes
the seam; it **edits no code**, stands up no process, and changes no runtime
behaviour. It does not move provider routing (ADR-0012 Context §5, unchanged), does
not rename anything, and does not migrate data. The gateway adapters are untouched.

## Consequences

- **Positive:** the operator surface acquires a real, self-hosted product on the
  other side of a process boundary without our tree absorbing its UI or its
  runtime. Upstream upgrades cost nothing in our repository — no rebase, no
  identity reconciliation — which is the whole point of choosing a boundary over a
  fork or an embed. The seam is machine-readable (three JSON Schemas) so the
  contract can be validated rather than trusted. ADR-0012's rule is honoured, not
  bent: the fleet's routing/claim/budget surfaces remain the single authoritative
  engines, and the mapping is recorded in both directions.
- **Negative:** a **process boundary** is a new operational surface — a second
  service to run, health-check and version, and a cross-boundary auth integration
  to get right once (two auth models meet here). Upstream is not wired into this
  repo at run time by this decision, so the seam is a contract with a real
  availability assumption to manage later. The mapping is not perfect: the seam doc
  names every **mismatch** where fleet shapes differ from upstream's, and those
  mismatches are adapter work, not free.
- **Neutral:** nothing is renamed, no transport changes (ADR-0011 stands), no data
  migrates, and the gateway adapters are untouched by explicit scope. The fleet
  keeps running while the seam is built — the migration is non-blocking, exactly as
  ADR-0012 (d) required.

**Follow-ups (named):** the owner of the **actual adoption** — standing up
`npx paperclipai onboard --yes`, wiring auth across the boundary and exercising the
three contracts end-to-end — is the fleet/platform operator for M26, tracked as the
child issue this record is the ADR for; **#370 freezes the seam, it does not run the
process**. The three schemas must be validated by `scripts/check-paperclip-integration.sh`
in `make verify`, so the seam cannot drift from the doc. The mismatch list in the
seam doc is the adoption's work-order.

**Reversibility.** The decision is **reversible at bounded cost**. The seam is three
contracts plus an adapter, so reversing to embed or fork — or to a different
operator surface entirely — replaces one adapter implementation rather than
rewriting a contract; and because nothing in the live loop depends on the seam
existing, reversal never stops the fleet. That is the same reversibility shape
ADR-0011 used for transport and ADR-0012 used for routing, and it is why adoption
was safe to accept now rather than to defer.
