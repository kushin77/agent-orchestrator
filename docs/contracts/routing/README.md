# The routing seam contract (frozen)

The frozen contract for routing in this repo: the two sides of the seam, the
fields each side writes, and the rule that exactly **one side writes each
field**. It exists because a peer is about to extract a second tier-routing
engine — `kushin77/hermes-agents#2` ("extract the tier-routing brain as a
standalone reusable package") — and two authoritative routing engines is
**half-coupling**: the one outcome that makes a later migration unrunnable. This
contract and [ADR-0015](../../decision-records/ADR-0015-routing-seam-single-authority.md)
freeze who owns what *before* the second engine exists.

## The seam, and the two sides

| Side | Who | What it does |
|---|---|---|
| `caller` | the fleet work-issuer: the dispatch loop and the brain (`governance/dispatch/`, `fleet/brain.py`) | asks for a decision; writes the **request** fields |
| `authority` | this repo's single routing authority: the `RoutingPolicy` port implementation (`gateway/sme-routing`) | produces the **decision** and owns the **policy** fields |

The peer package `kushin77/hermes-agents#2` is **neither side**. ADR-0015 decides
it is a **policy source we map** — its patterns may inform our declared policy,
recorded with provenance, but it produces no field of this contract. It is
recorded as a `source` in the ownership map, never as a `producer`. Consuming its
runtime is rejected explicitly by ADR-0015.

## Files

| File | What it freezes |
|---|---|
| `routing-request.schema.json` | the **inputs** — what the caller asks with |
| `routing-decision.schema.json` | the **outputs** — what the authority answers with |
| `policy-fields.json` | the **policy** vocabulary the authority reads (not wire fields) |
| `ownership.json` | the **one-writer map** — the single producer of every field above |
| `*.example.json` | instances the gate validates against the schemas |

## Inputs (written by `caller`)

| Field | Type | Meaning |
|---|---|---|
| `task_type` | string | the kind of work; the authority's router task `type` |
| `complexity` | integer 0..100 | the caller's difficulty estimate; the authority maps it to a band |
| `capability` | string | the capability the work requires (registry capability id) |
| `tier` | `flash` / `pro` / `auditor` | the tier requested — a **floor**, never a ceiling |

## Outputs (written by `authority`)

| Field | Type | Meaning |
|---|---|---|
| `tier` | `flash` / `pro` / `auditor` | the effective tier the authority selected |
| `model` | string | the concrete model id within the tier |
| `escalation` | object | `status` / `ladder` / `terminal` — what the ladder did and where it ended |

## Policy (owned by `authority`)

`fail_safe_route`, `complexity_to_tier`, `task_overrides`,
`tier_escalation_ladder` — the declared policy the authority reads. See
`policy-fields.json` for each field's live declaration under
`gateway/sme-routing/policies/`. **This seam changes nothing there**: the router
is the authority (issue #426 writes no file under `gateway/sme-routing/`; any
router change is a separate issue on that lane).

## The one-writer rule

> Exactly one side produces each field. A field with two producers is a second
> authority — half-coupling. A field with no producer is unowned.

`scripts/check-routing-seam.sh` enforces it: it gathers the field vocabulary from
the two schemas and `policy-fields.json`, then requires `ownership.json` to name
**exactly one** producer for each — refusing, **by name**, a field with two
producers and a field with no producer. The gate is offline and deterministic and
is tri-state: `0` OK, `1` NOT-OK, `2` CANNOT-ASSESS (an absent or unreadable
contract is never a pass).

## Related

- [ADR-0015](../../decision-records/ADR-0015-routing-seam-single-authority.md) — the decision this contract freezes.
- [ADR-0012](../../decision-records/ADR-0012-hermes-paperclip-boundary.md) — the rule it applies ("map the policy, do not couple the runtime").
- [Cross-repo execution boundary](../../CROSS-REPO-EXECUTION-BOUNDARY.md) — NG4: a peer's work is a direction issue on their board, never an edit here.
