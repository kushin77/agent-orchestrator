# Module admission — the parent-side sub-module contract

Status: **published and gated** (2026-09-14). Lane: issue #423 (parent #422).
The machine-readable register this document governs is the root `module.json`
(`parent`, `submodules`, `dependencies`); the gate that enforces it is
`scripts/check-module-admission.sh`.

This is the **parent side** of the sub-module contract. It exists because four
peer repositories filed a declaration request against a contract that did not
exist yet, so there was nothing for them to declare against and the parent's
`module.json` was a trust statement rather than a checked fact:

- `kushin77/hermes-agents` — "Declare as CMR sub-module of agent-orchestrator
  (module.json + parent declaration)" (issue `kushin77/hermes-agents#3`)
- `kushin77/deepseek` — same (issue `kushin77/deepseek#1`)
- `kushin77/ollama` — same (issue `kushin77/ollama#429`)
- `kushin77/paperclip` — the same request plus an open question about whether it
  is a sub-module at all (issue `kushin77/paperclip#10`, queried by the hub in
  `kushin77/CMR#794`)

The membership question is already load-bearing at the hub: `kushin77/CMR#952`
("Portfolio (master view) membership = module membership") cannot be resolved
while "what is a sub-module" is answered only by prose. This document and its
gate are the parent's answer.

## 1. What a sub-module is

A **sub-module** is a peer repository that:

1. publishes its own `module.json` (`cmr.module/v1`), and
2. that manifest carries a `parent` block naming this repository
   (`kushin77/agent-orchestrator`) as its parent, and
3. this repository has **admitted** it by recording the relationship in the root
   `module.json` `submodules` register with `admission: "declared"`.

All three are required. A repository that has only filed the request is not yet
a sub-module; a repository this repo merely `dependencies`-pins is not a
sub-module either. **Membership is the recorded admission**, not the request and
not the topic of an issue.

A sub-module is **not**:

- an external product this repository consumes (that is an integration, not a
  child), and
- a runtime dependency (see §5) — membership is a declaration, not a coupling.

### 1.1 The three admission states

| `admission` | Meaning | Gate behaviour |
|---|---|---|
| `declared` | Admitted: the peer manifest exists **and** declares this parent, and this repo pins its version. | Required facts must all hold; otherwise NOT-OK naming the module. |
| `requested` | The peer has filed its declaration request; the parent has **not** admitted it yet (no peer manifest, or the manifest does not declare this parent). | Accepted, reported as pending. Promoted to `declared` the moment the peer manifest declares this parent — the gate refuses a stale `requested`. |
| `independent` | An explicit answer that the peer is **not** a sub-module (an independent product / external serving layer). | Requires a `rationale` reference, and refuses any peer manifest that in fact declares this parent. |
| `undeclared` | A register entry with no declaration (or a missing `admission`). | **NOT-OK**, naming the module. A register entry may never be left undecided. |

## 2. What the parent declares

The parent's root `module.json` gains three additive keys (existing keys are
never modified — the file is consumed by other tooling):

```json
"parent": {
  "schema": "cmr.module.admission/v1",
  "module_id": "agent-orchestrator",
  "repo": "kushin77/agent-orchestrator",
  "uplink": "kushin77/CMR",
  "contract": "docs/MODULE-ADMISSION.md"
},
"submodules": [ { "id": "...", "repo": "...", "admission": "...", "request": "owner/repo#N", "repo_exists": true, "peer_manifest": false, "peer_parent": null, "module_version": null, "evidence": "..." } ],
"dependencies": [ { "module": "<declared sub-module id>", "kind": "contract", "pin": "vX.Y.Z" } ]
```

- `parent` — the parent-side declaration block: the schema tag, this module's
  id and repo, its own `uplink` (this repository is itself a governed CMR vendor
  module, `CMR:ONBOARD-0012`, tracked by issue `kushin77/agent-orchestrator#98`),
  and the path to this contract.
- `submodules` — the register. One entry per peer, each carrying the measured
  peer facts (`repo_exists`, `peer_manifest`, `peer_parent`, `module_version`)
  and the evidence string recording where they were measured.
- `dependencies` — the version pins the parent declares for the children it has
  admitted (see §4).

## 3. What the child declares

The **child** owns its half and files it on **its own board** — this repository
never edits a peer (see §7). A child's declaration is:

1. a `module.json` at the child repo root, valid against `cmr.module/v1`, and
2. a `parent` block in that manifest naming `kushin77/agent-orchestrator` as the
   parent, and
3. the declaration request issue on the child's own board (the `request`
   reference the register records).

The parent does not write any of it. The child-side change for the three
requesting peers is tracked by `kushin77/hermes-agents#3`,
`kushin77/deepseek#1` and `kushin77/ollama#429`.

## 4. How versions pin

The parent pins a child in `dependencies` **only after** admitting it. An entry
is `{"module": "<id>", "kind": "contract", "pin": "vX.Y.Z"}` where:

- `module` must name a register entry whose `admission` is `declared`, and
- `pin` must equal that entry's recorded `module_version` (its own manifest's
  `versions.latest`).

A pin that names an unregistered module, an unadmitted module, or a version the
child does not declare does not resolve and fails the gate. Every `declared`
child must carry a resolving pin — an admitted child that is not pinned is a
finding. Because no peer has been admitted yet (see §6), `dependencies` is
currently empty and the gate enforces that this changes in lockstep with the
register.

## 5. What a sub-module must NOT inherit

A sub-module inherits **nothing** from its parent. Specifically it does **not**
inherit identity, secrets, or tenancy:

- **Identity** — a sub-module has its own repository identity, its own
  `module.json` id, and its own provenance. It never assumes the parent's
  identity, and the parent never assumes the child's.
- **Secrets** — no credential, token, key, or secret crosses the boundary, in
  either direction. Secrets live in environment variables or a secret manager
  (AO-GR-7), never in a manifest, a register entry, or this contract.
- **Tenancy** — a sub-module is not a tenant of this control plane and does not
  inherit the parent's tenants, data, budgets, audit, or memory. Tenant
  isolation is structural (AO-GR-15) and is not extended by module membership.

This is the same rule ADR-0012 applies to runtimes (`docs/decision-records/ADR-0012-hermes-paperclip-boundary.md`):
a peer may be a declared owner of a *contract* or a *pattern source*, and it is
still **"map the policy, do not couple the runtime"**. Membership declares a
relationship; it never confers identity, credentials, or tenancy.

## 6. The register, measured (2026-09-14)

| id | repo | admission | peer `module.json` | peer `parent` |
|---|---|---|---|---|
| hermes-agents | `kushin77/hermes-agents` | requested | absent (HTTP 404) | — |
| deepseek | `kushin77/deepseek` | requested | present (`v0.1.0`) | absent |
| ollama | `kushin77/ollama` | requested | absent (HTTP 404) | — |
| paperclip | `kushin77/paperclip` | independent | absent (HTTP 404) | — |

The measurement is deliberate and honest: **no peer has been admitted**, because
none of the three requesting peers yet ships a manifest whose `parent` names this
repository. `deepseek` has a manifest but no `parent` block; `hermes-agents` and
`ollama` have no manifest yet. Calling any of them `declared` today would be a
claim, not a fact — which is exactly the defect this gate removes.

### 6.1 The paperclip answer (explicit)

**`kushin77/paperclip` is not a sub-module of `kushin77/agent-orchestrator`.** It
is recorded `admission: "independent"`.

Reasoning: paperclip is consumed as an **external serving layer** — the
paperclip.ing adapters (`docs/decision-records/ADR-0013-paperclip-ing-integration.md`
and the shipped paperclip integration) sit on the other side of the seam and are
integrations, not children. `kushin77/paperclip#10` asks the relationship question and
`kushin77/CMR#794` raises the same doubt from the hub side ("appears to be an
independent product"), so the parent answers explicitly rather than leaving a
register entry undecided. The gate refuses this answer the moment a paperclip
manifest declares this repository as its parent, so the decision is enforced and
reversible only by an explicit register change.

## 7. The boundary (NG4)

This contract and its gate are the parent side **only**. No peer repository is
edited, and no issue is filed in a peer from this lane — `docs/CROSS-REPO-EXECUTION-BOUNDARY.md`
§1 and AGENTS.md rule 14 (NG4): *a repo remediates findings for itself only*.
Anything the parent needs from a child is a **direction issue on that child's
board**, recorded in the register's `request` reference. The child-side
declaration is the child's issue; this lane publishes the contract and the
parent-side registry the children declare against.

## 8. The gate

`scripts/check-module-admission.sh` validates the committed declaration against
this document. Exit-code contract: **0 OK / 1 NOT-OK / 2 CANNOT-ASSESS**;
CANNOT-ASSESS never exits 0 and a missing or unavailable peer source is never a
pass.

- **Default mode** (`bash scripts/check-module-admission.sh`) — offline and
  deterministic. Validates schema, shape, the three-state admission rule,
  reference integrity (`request` filed on the peer's own board; each repo
  published in this document), the dependency pins, and the recorded peer facts.
  A missing recorded fact is CANNOT-ASSESS, never OK. It also runs its own
  provoked negative controls, so a check that cannot fail cannot pass (AO-GR-4).
- **`--verify-peers`** — the live mode: `gh api` each peer repo and its
  `module.json`, and refuse a `declared` child whose peer publishes none. It
  needs the network; with `--peer-facts FILE` it runs the *same* code path from
  a recorded facts file, so the refusal is reproducible offline.

The gate is the enforcement seam for the epic's definition of done — *"a peer
can declare itself a sub-module of this repo against a published, gated
contract"*: the peer declares, `module.json` records the measured truth, and the
gate refuses any claim that does not hold.
