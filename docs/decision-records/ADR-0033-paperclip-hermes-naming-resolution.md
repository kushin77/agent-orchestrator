---
id: ADR-0033
status: accepted
date: 2026-09-20
deciders: [owner]
req: []
supersedes: []
---

# ADR-0033: Paperclip/Hermes naming resolution — one name, one authoritative artifact

## Status

`accepted` — Owner ratified on 2026-09-20 (issue #1514, a child of epic #1510).

**This record resolves naming; it does not re-decide architecture.**
[ADR-0012](ADR-0012-hermes-paperclip-boundary.md) and
[ADR-0013](ADR-0013-paperclip-ing-integration.md) already scoped the
architecture correctly — `fleet/brain.py` + `governance/dispatch/` is the
top-level orchestrator, and Paperclip is the ticket/heartbeat/budget operator
surface only. Those decisions stand **unamended**; their statuses do not change.
This record fixes the **naming collision** those two ADRs never explicitly
resolved, and it supersedes the **org-wide claim** that "Paperclip (or Hermes)
is the top-level orchestrator of all engineering work" — a claim that
contradicts the accepted ADR-0013 scope, which forbids the upstream product from
acting as "a second authoritative runtime".

**Numbering note:** the next free number in sequence was `0033`; `0032` is taken
by [ADR-0032](ADR-0032-fleet-loop-ownership.md) (issue #1512). A tree-wide grep
(excluding `vendor/`, `.research/`, `.board/`) finds zero citations of
`ADR-0033`, so this record takes `0033`.

## Context

Two names — "Paperclip" and "Hermes" — each accreted several **unrelated**
artifacts over time with no single naming authority, so different people and
docs use the same word for different things. The two review inventories
(PAPERCLIP-REVIEW-2026-09-20.md, HERMES-REVIEW-2026-09-20.md) each flagged the
collision: Paperclip is four distinct things, Hermes is three. ADR-0012 and
ADR-0013 scoped the *architecture* (who owns dispatch, what the upstream
products may do) but never enumerated the artifacts by path, so the collision
persisted as a documentation gap rather than a decided boundary.

### The four "Paperclip" artifacts

| # | Artifact | Path | What it is | Authoritative for "agent control"? |
|---|---|---|---|---|
| P1 | Fleet agent persona | `registry/profiles/seeds/paperclip.1.0.0.yaml`, `registry/personas/cards/paperclip.yaml` | The **research / docs-authoring persona** (LOW tier) in the five-agent team, bundled in `registry/packs/releases/purebliss-team.1.0.0.yaml` | No — a worker persona, dispatched by the brain |
| P2 | Gateway provider adapter | `gateway/providers/paperclip.py` | An **OpenAI-compatible inference adapter** routing the `paperclip-planner` model | No — inference transport only |
| P3 | Vendored CMR planning module | `vendor/CMR/catalog/modules/paperclip/` | A **pinned, read-only hub module** declaring planning / roadmap / status-report patterns (GR-5: never edited) | No — a pattern source |
| P4 | Upstream product | `paperclipai/paperclip` (`paperclip.ing`) | The **external, MIT, self-hosted SaaS** analysed in [`../PAPERCLIP-ING-GAP-ANALYSIS.md`](../PAPERCLIP-ING-GAP-ANALYSIS.md) | No — ADR-0013 forbids it from code execution or acting as "a second authoritative runtime" |

The boundary between P1 and P2 is already recorded in ADR-0012; the P1–P4
enumeration is reproduced in
[`../PAPERCLIP-ING-GAP-ANALYSIS.md`](../PAPERCLIP-ING-GAP-ANALYSIS.md) §
"Namesake disambiguation".

### The three "Hermes" artifacts

| # | Artifact | Path | What it is | Authoritative for "agent control"? |
|---|---|---|---|---|
| H1 | Vendored capability-registry spec | `vendor/CMR/catalog/modules/hermes-agents/` | The **pinned routing / capability-registry / escalation / model-tiering contract** ADR-0012 adopts ("map the policy, do not couple the runtime") | No — a contract and pattern source, never a runtime |
| H2 | Keyless local provider adapter | `gateway/providers/hermes.py`, catalog entry `gateway/catalog/modules/hermes/module.json` | A **keyless, Ollama-compatible `/api/chat` inference adapter** for the `hermes3` LLM (frozen fallback `hermes → hermes/ollama`) | No — inference transport only |
| H3 | Upstream product | `kushin77/hermes-agents` (repo) | The **real, separate Flask service** — measured deployable-not-running by ADR-0012 Context §7; docker-compose service `hermes-agent`, port `9501`, VIP `192.168.168.50` | No — not wired into this repo at all |

### The `hermes-agent` / `hermes-agents` split, measured

The HERMES-REVIEW inventory names the "real separate product" as
`kushin77/hermes-agent` (singular). That repository **does not exist**
(`gh api repos/kushin77/hermes-agent` → HTTP 404). The real repository is
`kushin77/hermes-agents` (**plural**), and `hermes-agent` (**singular**) is only
the **docker-compose service / container name** *inside* that repository
(`docker-compose.yml` `services.hermes-agent`, `container_name: hermes-agent`,
`PORT=${PORT:-9501}`, healthcheck `GET /health`). The singular/plural confusion
is the exact collision this record exists to end.

A fourth, distinct surface — the **`hermes-agents` service client** behind a
transport seam at `integrations/hermes/` (issue #942) — is the client half of
H3's integration, not a separate authoritative engine.

### The "top-level orchestrator" claim

The org-wide claim "Paperclip is the top-level orchestrator of all engineering
work" contradicts ADR-0013, which states the upstream product is the
ticket/heartbeat/budget operator surface only and is **never** a second
authoritative runtime. Measured on the current branch, a repo-wide grep for
`top-level orchestrator` returns a single hit —
`engine/multiagent/tests/test_orchestrator.py` (a module docstring about generic
"hierarchy + optional consensus verdict" composition, **not** a Paperclip/Hermes
claim) — and **zero** hits under `docs/`. The claim lives outside this repo; this
record makes the correction durable here.

## Decision

**There is exactly one top-level orchestrator of agent work: `fleet/brain.py` +
`governance/dispatch/`.** Neither "Paperclip" (P1–P4) nor "Hermes" (H1–H3) is
the top-level orchestrator; no artifact of either name is authoritative for
agent control.

**Each name resolves to a single authoritative artifact per context:**

1. **"Paperclip" as a runtime authority does not exist.** P1 (persona), P2
   (provider adapter), P3 (vendored module) and P4 (upstream `paperclip.ing`
   product) are each authoritative only for their own surface — worker persona,
   inference transport, planning/reporting patterns, and the external operator
   surface respectively — and **none** controls dispatch or agent work. Any
   doc or claim asserting "Paperclip is the top-level orchestrator" is **wrong**
   and must cite this record.
2. **"Hermes" as a runtime authority does not exist in this repo.** H1 (vendored
   contract) is mapped, never coupled; H2 (provider adapter) is inference
   transport only; H3 (the `hermes-agents` product) is deployable-not-running
   and not wired in (ADR-0012 Context §7). The `hermes-agents` repository is the
   **only** valid repo spelling; `hermes-agent` (singular) is a docker-compose
   service name inside it, not a repository.
3. **The `source.repo` field of `gateway/catalog/modules/hermes/module.json` is
   `kushin77/hermes-agents` (plural) and is correct** — it points at the real
   repository from which the adapter harvests its routing vocabulary. It is
   annotated (not changed to `hermes-agent`) because `kushin77/hermes-agent`
   does not exist; the singular form is only the service name.

**Explicitly out of scope:** this record edits no dispatch, routing, or gateway
behaviour; it renames nothing; it flips no architecture status. ADR-0012 and
ADR-0013 remain `accepted` and authoritative for scope.

## Consequences

- **Positive:** the phrase "Paperclip" or "Hermes" no longer has to be decoded
  from context — each maps to one of the enumerated artifacts above, with
  "agent control" unambiguously owned by `fleet/brain.py` +
  `governance/dispatch/`. The singular/plural `hermes-agent`/`hermes-agents`
  trap is recorded so no future doc cites a non-existent repository. The
  org-wide "top-level orchestrator" claim is corrected on the record.
- **Negative:** this record is documentation, not enforcement — a future doc can
  still write "Paperclip is the orchestrator" unless a mechanical glossary gate
  lands. It renames nothing, so the four `paperclip` and three `hermes` paths
  keep their overloaded spellings; disambiguation is by the table above, not by
  path rename.
- **Neutral:** ADR-0012/0013 statuses, the `RoutingPolicy` port, provider
  routing, and all gateway adapters are untouched by explicit scope.

**Follow-ups.**

- A mechanical glossary/naming-registry check would catch a third artifact
  landing under an already-used name at the moment it is added (the earlier
  catch this issue's root cause calls for) — candidate future work, not this
  lane's file.
- `~/ss-wt4214/infra/modules/paperclip/` ("Paperclip Memory Bridge", issue
  #1950) is a **separate, non-conflicting initiative** — a Terraform module
  deploying a `paperclip` service to a remote host (`paperclip_port` 17436,
  `shared-services-net`), outside this repository. It is **not** P1–P4 and does
  not claim orchestration authority; its auto-generated README stub
  (`[UPDATE]` fields) is owned by that repository's board, not this lane. This
  record cross-references it so the name does not silently become a fifth
  "Paperclip" here.
- [`../PAPERCLIP-ING-GAP-ANALYSIS.md`](../PAPERCLIP-ING-GAP-ANALYSIS.md) gains a
  pointer to this record from its "Namesake disambiguation" section (same
  change).
