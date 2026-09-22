# RCA: legacy/redundancy gap review — agent-orchestrator (2026-09-21)

Parent: #1906 (epic). Relates to #1539.

## Relationship to #1539

#1539 ("code-knowledge: dedupe pass using indexer + headers") is a
cross-repo (agent-orchestrator + shared-services + shared-frontend)
mechanism-driven sweep, scoped to three known clusters (edge-header
contract, commit-message-rule validators, baseline-mechanism formats) and
gated on the CMR indexer/code-header rollout being queryable. This review
is a separate, broader, intra-repo (agent-orchestrator only) manual sweep
for functional overlap and legacy code, not dependent on indexer state.
No overlap in findings: #1539's three clusters are cross-repo files this
review never touched. Nothing here needed to be filed against #1539;
findings are tracked under new epic #1906 instead.

## Step 1 — functional overlap

**Flag-reader triplication (suspected, ruled out).** `gateway/providers/flags.py`,
`portal/server/config_flags.py`, and
`integrations/paperclip/adapters/sync/flags.py` are three independently
implemented fail-closed flag readers. Read all three in full: each carries
a `---knowledge---` header with `derives_from` and an explicit `gotchas`
line stating it deliberately never imports the sibling reader, so each
flag-gated surface's boot never depends on importing another surface's
module. This is documented policy (avoid coupling one surface's boot to
another's), not accidental duplication. No finding filed.

**`gateway/proxy/resolver.py` + `gateway/proxy/wiring.py`.** Looked like a
duplicate `resolve(...)` pair; `resolver.py` is a `@runtime_checkable
Protocol` declaring the seam (issue #16), `wiring.py` provides the
concrete adapter. Legitimate interface/implementation split, not overlap.

**`fleet/terminal.py:resolve_capacity` vs `fleet/capacity.py:resolve_capacity`.**
Same name, not duplication: `terminal.py`'s version is a thin wrapper that
calls `capacity.resolve_capacity(...)` with the live ready-lane set and
focus config. Composition, not reimplementation.

**Broader `def classify|resolve|route` grep** (about 90 hits across
gateway/, governance/, registry/, identity/, fleet/, integrations/,
control-plane/) turned up no other near-identical bodies on inspection of
the highest-risk pairs (same name, unrelated files). Most `resolve*` names
are domain-specific (agent resolution, prompt-module resolution, RBAC
scope resolution, ref resolution) with divergent bodies — same verb,
different concept, correctly not flagged.

## Step 2 — legacy code

Ran `grep -rln "deprecated\|legacy\|TODO.*remove\|obsolete"` across the
repo (excluding vendor/, tests). ~35 files hit. Read the matched lines in
each: the overwhelming majority are live, actively-exercised compatibility
shims with their own tests and headers — e.g. `fleet/channel.py`'s
`SCHEMA_VERSION_LEGACY`/`ROLES_LEGACY` dual-dialect negotiation,
`governance/dispatch/claims.py`'s dual-read of the frozen single-file
ledger plus the newer claims directory, `telemetry/metering/model_aliases.py`'s
`LEGACY_MODEL_ALIASES` (actively imported and called by
`gateway/providers/config.py`'s `normalize_model`, not dead), and
`registry/packs/skills.py`'s `deprecated` lifecycle terminal state (a real,
reachable state machine value). These are deliberate compatibility/lifecycle
code, correctly still present — not gaps.

**Confirmed gap: `gateway/catalog/modules/hermes/module.json` declares
`"status": "retired"`** while `gateway/providers/hermes.py`,
`gateway/providers/flags.py` (`hermes_enabled`, wired into
`gateway/providers/registry.py`'s constructor), and a full
`integrations/hermes/` package (mapping/policy/model/audit/cli/client/sync)
are live, wired, and under active development — not orphaned residue.
~60 files reference `hermes`. The provider is gated off by default
(fail-closed flag), so nothing ships live by accident, but the manifest
status contradicts the amount of live investment. Filed as #1907
(needs-a-decision: reconcile the status field, or the live buildout is
misplaced on a module the manifest calls retired).

## Step 3 — dead-code sample

Per the "sample, don't do a full sweep" instruction, sampled 20 of the
`classify*`/`resolve*`/`route*` functions found in Step 1's grep and
checked each for direct-name call sites elsewhere in the repo (excluding
its own file, vendor/, and tests). **7/20 (35%)** showed zero external
callers by direct-name grep: `gateway/sme-routing/router.py:classify_squad`,
`infra/fleet/dev_run.py:classify_changes`,
`governance/knowledge/crossref.py:classify_marker_target`,
`fleet/runner/verify.py:classify_conclude`,
`engine/core/tickets/handlers.py:resolve_decomposer`,
`integrations/erp/tx/definitions.py:resolve_chain`,
`portal/server/livestore.py:resolve_seed_path`. This is a lead, not a
verdict — direct-name grep misses CLI dispatch tables, reflective/dynamic
calls, and doesn't itself confirm dead status. Filed as #1909
(needs-a-decision, one PR per function once triaged).

## Issues filed

- #1906 — EPIC: retire overlap and legacy code — measured, not guessed
- #1907 — hermes module.json status:retired vs live-wired provider (needs-a-decision)
- #1909 — 7/20 sample dead-code candidates need per-function triage (needs-a-decision)

No safe-to-remove findings were confirmed in this pass; both filed issues
are needs-a-decision by design — nothing here is a same-PR delete.
