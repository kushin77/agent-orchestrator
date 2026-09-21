# Paperclip promotion decision — go/no-go (issue #1515)

**Superseded 2026-09-21.** The enabled-by-default policy reversal
(policy-gr5-enabled-by-default, GR-5/AO-GR-6) is an explicit owner decision,
made the day after this record, that new capabilities ship enabled by
default. The owner confirmed directly that it supersedes this NO-GO:
`enable_paperclip` now defaults `true` in `infra/terraform/variables.tf` and
`infra/feature-flags/registry.yaml`. The text below is kept as history.

**Original decision (2026-09-20, superseded):** NO-GO — `enable_paperclip` is
**not** promoted to a real environment at this time; the flag stays
`default = false` (documented-inert).

**Owner sign-off:** kushin77 — recorded by the merge of the PR closing #1515
(the decision doc itself is the record; no separate approval mechanism is
required, per the issue's assignment convention).

## Promotion decision

Promoting `enable_paperclip` provides **no value today** under the scope ADR-0013
freezes for the upstream product, and the 2026-09-20 paperclip friction audit
(§2) found **no current consumer** depending on the self-hosted runtime. The
flag therefore remains OFF. This is a deliberate choice, not an oversight.

## Evidence

1. **Scope is narrow and already frozen** — `docs/decision-records/ADR-0013-paperclip-ing-integration.md`
   (`accepted`) adopts the upstream Paperclip CLI as an **external operator
   surface** over a process boundary, constrained to exactly three contract
   shapes — heartbeat, ticket, budget — and is explicitly *never* a
   code-execution runtime or a second authoritative orchestrator. The normative
   seam is `docs/PAPERCLIP-ING-INTEGRATION.md`.
2. **No current consumer** — the audit found no ticket/heartbeat/budget
   integration depending on the self-hosted runtime. A tree scan of `gateway/`,
   `engine/`, `guardrails/`, `telemetry/`, `control-plane/`, and `portal/` finds
   paperclip referenced only in guardrail/policy tests and a projection comment
   (`telemetry/audit/read_model.py`) — no production code path calls the
   runtime's API.
3. **Inert by construction** — the runtime module
   (`infra/terraform/main.tf` `module "paperclip_runtime"`) is count-gated on
   `var.enable_paperclip`; with the flag OFF (the committed default) `terraform
   plan` shows zero resources. The guardrail bundle
   (`guardrails/policy/bundles/platform/paperclip-operator.yaml`) is also
   default-OFF, and the feature-flags registry row (`services.paperclip`) records
   `default: off`.

## Consequence

- `enable_paperclip` remains `default = false` (negative control: `grep
  enable_paperclip infra/terraform/variables.tf` still shows `default = false`).
- The code path is **not** removed — it is kept documented-inert, ready to
  promote when a concrete ticket/heartbeat/budget integration need lands.
- A future promotion is a **separate go decision**: it would run `terraform plan`
  with `enable_paperclip = true` (never `apply` — GR-5), capture and review the
  plan, and file a follow-up apply issue.

## Relationship to E1 (#1514)

This decision relies on ADR-0013's already-accepted scope (ticket/heartbeat/
budget only, never code execution). E1 (#1514) resolves *naming* across the
conflated "paperclip" artifacts; it does not change whether a consumer exists
today, so it does not change this no-go.
