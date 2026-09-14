# Cross-repo: what the peer `kushin77/deepseek` open board offers the command center CLI

Issue #360. This document reviews the open board of the peer repository
**`kushin77/deepseek`** and maps every item that bears on **remote control or
dispatch** onto the surfaces that already exist here. For each item it states,
separately and non-interchangeably:

- **our side** — what `kushin77/agent-orchestrator` already has, if anything;
- **their side** — what only the peer can do (any edit to their tree is theirs);
- **the contract we consume** — the precise interface, and whether it is
  **stable and versioned**;
- **disposition** — one of `link-only`, `consume`, `direction-issue-needed`,
  `blocked`;
- **what it unlocks for the command center CLI** — one line.

Every mapping below is grounded in the peer issue text or in a file that exists
in this checkout. Where a claim could not be evidenced it is recorded in
§8 rather than smoothed over.

## 0. The boundary this document lives under (NG4)

[`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) is the
governing contract: **a repository remediates findings for itself only.** The
only sanctioned output of observing a peer's need is a **direction issue on the
peer's board** — never an edit, a push, or a pull request authored from here. So:

- We build **here**. We **link** to them. We never vendor, fork or mirror their
  code (GR-10 provenance discipline) — `vendor/` is pinned read-only and the
  peer is not in it.
- **Cross-repo `Closes` is never written.** A `Closes` / `Fixes` / `Resolves
  <owner>/<repo>#<n>` reference to a foreign repo would auto-close a peer issue
  from here; [`CROSS-REPO-LESSONS-SYNC.md`](CROSS-REPO-LESSONS-SYNC.md) §6
  refuses it by name (`peer-close-refused`).
- Nothing in this document changes the peer board. The two peer items it relies
  on (#118, #119) were filed by earlier lanes and are **recorded**, not created
  here (§6).

**Citation rule.** Paths under `vendor/` are cited in backticks and never as
markdown links: a fresh worktree leaves the submodule unpopulated, so a relative
link into it resolves on a populated checkout and breaks the docs gate on a
clean one. Measured while writing this document: `vendor/CMR/channels/` is
**absent** in this lane's worktree.

## 1. How to read a mapping

| Disposition | Meaning |
|---|---|
| `link-only` | We cite the peer item for context. Nothing here builds against it and nothing must exist first. |
| `consume` | We build against an interface. The interface is named, and its stability is stated. |
| `direction-issue-needed` | The peer half only the peer can deliver is not yet requested. The exact proposed direction issue — title, labels, body — is written out in §6 for a human to approve. |
| `blocked` | The mapping cannot proceed, and the blocker is named. |

## 2. What "the command center CLI" means in this repository

The remote control command center is the fleet control surface, and it is already
shipped. It is five surfaces over one contract:

| Surface | What it is |
|---|---|
| `fleet/control.py` | the operator's control plane — `refresh`, `update`, `poke`, `halt`, `debug`, `watch`, `health`, `live` |
| `fleet/terminal.py` | the dispatch loop: watch → provision → dispatch one subagent → gate → report |
| `fleet/console.py` | the one-frame live console (`render(snapshot)`) |
| `fleet/health.py` | the tri-state health signal (0 healthy / 1 degraded / 2 failing) |
| [`fleet/CONTRACT.md`](../fleet/CONTRACT.md) | the normative contract: roles (`operator` → `brain` → `sister` → `subagent`), the six directive verbs, the FinOps allowlist (`tier` ∈ {`pro`, `flash`}, `thinking` ∈ {`none`, `low`, `high`}) |

Two properties of that surface matter for every mapping below:

1. **The dispatcher already speaks a runner argv.** `build_command()` returns
   `shlex.split(runner) + [prompt]`, `run --runner` defaults to `claude -p`, and
   `--dry-run` prints the exact argv it would execute. A peer executor is
   therefore pluggable at a seam that already exists and is already asserted.
2. **The dispatcher already builds a context pack offline.** `issue_context()`
   renders issue title, body, the issue's own `Verify:` clause, and the matching
   ledger lessons — resolved from the **committed board snapshot**, with the live
   board only as a fallback, and a **named warning** when either is missing.

A peer capability reaches the command center along exactly three seams: the
**runner argv** (who executes), the **context pack** (what the executor is told),
and the **metering/budget record** (what the run cost). The six issues below map
onto those three seams.

## 3. The peer, measured

| Fact | Evidence |
|---|---|
| The peer is a declared peer of this repo | `governance/sync/peer-board/ownership.json` → `peers: ["kushin77/CMR", "kushin77/deepseek"]` |
| The peer board is reviewed by a standing pass | [`CROSS-REPO-SYNC-OWNER.md`](CROSS-REPO-SYNC-OWNER.md); `governance/sync/peer_triage.py` |
| `area:fleet` peer issues map onto our `fleet/` lane | `governance/sync/peer-board/ownership.json` → `lanes.fleet = "fleet/"` |
| The peer is **not** in the CMR direction-channel registry | `vendor/CMR/channels/spokes.tsv` carries **35 lines and no `deepseek` row**; `spokes.tsv`'s `direction_labels` column is therefore unset for this peer |
| The two direction issues we rely on carry **no labels** | `gh api repos/kushin77/deepseek/issues/118` and `/119` both report an empty `labels` array |
| The peer has no lessons ledger yet | no `docs/operations/lessons.md` and no lessons-ledger file in the local clone at `/home/akushnir/deepseek` |

The last two facts are the load-bearing ones for §6: the **documented auto-file
path is not unambiguous for this peer**, so this document **files nothing** and
recommends nothing be filed without a human decision.

The peer's open board holds 37 issues. Six bear directly on this document; eight
more bear on remote control or dispatch and are flagged in §5.

## 4. The six issues

### 4.1 `kushin77/deepseek#30` — Expose usage and cost through CLI and MCP resources

- **State:** open · **Labels:** `area:mcp`, `area:metering`, `status:planned` ·
  **Milestone:** EPIC-4 — Metering and FinOps enforcement

**Acceptance criteria (quoted):**

> - `deepseek-agent usage` reports spend for a window, grouped by tenant, agent, or
>   model, with unresolved prices reported separately and never folded into a total.
> - The `deepseek://usage` MCP resource serves the same data with the same groupings, so
>   a client and a human see one answer.
> - Totals with unresolved entries are labelled as incomplete rather than presented as
>   authoritative.
> - JSON output on both surfaces is stable in ordering and schema, and is asserted by
>   tests.

**Our side.** The honesty semantics the peer is being asked for are already frozen
here, in the metering lane:

- `telemetry/metering/cost_details.py` states the rule verbatim — *"unknown -> None,
  never 0"*, an unpriced call yields `priced=False` with every USD figure `None`,
  the aggregate then reports `costComplete=False`, and **"no surface may present an
  incomplete cost as a total."**
- `telemetry/metering/report.py` (`UsageReporter`) already exposes the three
  groupings the peer's criterion names — `by_tenant()`, `by_agent()`, `by_model()` —
  alongside `totals()` and `billing_summary()`.
- `telemetry/metering/model.py` (`UsageRecord`) carries the per-row honesty fields
  the "reported separately" requirement needs: `metered`, `cost_usd: Optional[float]`,
  `cost_source`, `unmetered_reason`.
- The cross-repo wire shape for that data is frozen at
  [`contracts/metering/README.md`](contracts/metering/README.md)
  (`metering-record.schema.json`, closed `additionalProperties: false`), with a
  one-writer-per-field map and a gate (`scripts/check-metering-parity.sh`).

**Their side.** Only the peer can make `deepseek-agent usage` and `deepseek://usage`
answer with the *same* groupings over a *window*, label an incomplete total as
incomplete, and pin the ordering and schema with tests. Measured today in the local
clone: both surfaces exist — `usage` is registered at
`src/deepseek_agent/cli/main.py` as an offline `--ledger`/`--json` subcommand with
`by_tenant` / `by_agent` / `by_model` / `by_tenant_model` rollups in
`src/deepseek_agent/usage_ledger.py`, and `deepseek://usage` is declared in
`RESOURCE_SPECS` at `src/deepseek_agent/mcp/resources.py` — but the resource serves
`self.client.usage.summary()`, a **process-local** summary, which is not the same
answer as the ledger query. Closing that gap is theirs.

**The contract we consume.** Two, at different maturity:

| Interface | Versioned / stable? |
|---|---|
| [`contracts/metering/metering-record.schema.json`](contracts/metering/README.md) — the frozen record shape | **Yes.** The shape carries a `schemaVersion` field and a `kind`; the schema is closed; `docs/contracts/metering/ownership.json` names `peer-deepseek-export` as the producer for the peer's own records, and `scripts/check-metering-parity.sh` refuses an unmapped field, a field with no producer, and a claim with empty evidence |
| The peer's `usage --json` output and `deepseek://usage` payload | **Not yet.** The peer's own criterion is that they become stable and test-asserted; that is the deliverable of #30, not a property it has today. We must not treat them as a contract until it lands |

**Disposition: `consume`.** We consume the frozen metering shape we already own;
the peer half is already requested by their **#119** (§4.6), which asks them to
emit exactly this shape. No new direction issue is needed.

**Command center CLI.** Unlocks a *spend* panel in `fleet/console.py` that can be
labelled `costComplete=False` instead of printing a total that silently drops
unpriced rows — the console stops being able to flatter the fleet's own numbers.

### 4.2 `kushin77/deepseek#52` — DS worker-tier integration with read-only default

- **State:** open · **Labels:** `area:fleet`, `area:iac`, `status:planned` ·
  **Milestone:** EPIC-9 — Fleet integration

**Acceptance criteria (quoted):**

> - The DS tier runs this module as its Python entry point, with `DS_WRITE_ENABLED=0`
>   as the documented default mode.
> - The credential shim contract is honoured: secrets arrive from the environment at run
>   time and are never exported into a shared shell or written into the repo.
> - A tier run leaves an auditable ledger record naming tenant, agent, tier, and model.
> - Unsetting the write flag yields read-only behaviour, proven by a test rather than
>   assumed.

**Our side.** This is the issue with the strongest existing overlap, because its
third criterion *is* a subset of our frozen record shape:

- *"an auditable ledger record naming tenant, agent, tier, and model"* is exactly
  `identity.tenantId` + `identity.agentId` + `attribution.tier` + `attribution.model`
  in [`contracts/metering/README.md`](contracts/metering/README.md), and
  `docs/contracts/metering/ownership.json` already names `peer-deepseek-export` as
  that record's peer producer.
- The *"never exported into a shared shell"* clause is this repo's standing secret
  doctrine (`AGENTS.md` GR-6; the fleet posture is that secrets live in the
  environment or a secret manager, never in a file or git history). It is a
  **policy we hold**, not a contract we consume.
- The read-only-default shape has a local analogue in
  `infra/feature-flags/registry.yaml`: `default_policy: off`, *"New entries MUST
  default to `off`"*, with a promotion path in `infra/rollout/`. That is the same
  fail-safe-default construction as `DS_WRITE_ENABLED=0`, applied to infra instead
  of to a worker tier.
- The receipt half of the record — the evidence that ties spend to a ticket — is
  produced here by `integrations/paperclip/budget.py` (#415), per the same
  ownership map.

**Their side.** The tier's Python entry point, the `DS_WRITE_ENABLED=0` default,
the credential-shim contract, and the test that proves unsetting the flag yields
read-only behaviour. The peer has already **decided** two of those four: its
`docs/adr/0008-ds-write-enabled-read-only-default.md` (Accepted) fixes that
`DS_WRITE_ENABLED=0` *and unset* both mean read-only, and
`docs/adr/0012-no-credential-shim-is-ported.md` (Accepted) declines to port the
shared-services shim while keeping its rule. #52 is the implementation that has to
match them.

**The contract we consume.** `docs/contracts/metering/metering-record.schema.json`
plus the ownership map — the same versioned, gate-enforced shape as §4.1, with
`receipt.ticket` / `receipt.receipt` / `receipt.evidence` produced by
`fleet-paperclip-adapter`. **Stable and versioned: yes.**

**Disposition: `consume`.** The ledger-record criterion is a field list we froze
before the peer's receipt could harden into a variant, and their **#119** already
asks them to emit it.

**Command center CLI.** Unlocks a per-tier *read-only* badge in the console: the
operator sees whether a dispatched tier could have written anything, and the
budget panel can tie its spend rows to a receipt instead of an assertion.

### 4.3 `kushin77/deepseek#53` — Dispatch integration: context pack and issue-hint consumption

- **State:** open · **Labels:** `area:fleet`, `status:planned` ·
  **Milestone:** EPIC-9 — Fleet integration

**Acceptance criteria (quoted):**

> - The module exposes a documented way for the fleet's dispatch tooling to obtain a
>   context block for an issue, so lanes start from a rendered hint rather than a pasted
>   snapshot.
> - The contract is stable and versioned, and a change to it is a versioned change.
> - Nothing in this path requires network access at build or gate time; the network call
>   is the dispatcher's, not the gate's.
> - A test proves the renderer is deterministic for the same input.

**Our side.** This is the one item where our side is *already the consumer*, and the
whole property the peer must supply is one we have already built the seam for — against
a different peer:

- The **local context pack already ships**: `fleet/terminal.py` `issue_context()`,
  `render_context_pack()` and `context_summary()` (issue #220) assemble the issue's
  title, body, `Verify:` clause and matching ledger lessons from the **committed board
  snapshot** — offline — with the live board read used only as a fallback and a
  **named warning** emitted when title or body is absent. That is the same
  "offline renderer, network is the dispatcher's" shape #53 asks the peer for.
- The **consumption pattern for a peer-published pack already exists**, frozen by
  ADR-0018 as *consume, never mirror*: `engine/memory/prompt_cache.py` declares
  `CONTEXT_PACK_SCHEMA = "codeidx.context-pack/v1"` and a `CONSUMED_CONTRACTS`
  register whose row names the producer (`kushin77/code-indexing`), the published
  contract (`#128`), and the honest state `UNVERIFIED` — and an **unknown declared
  schema id is refused by name** rather than consumed under a shape it may not
  match. `scripts/check-context-pack-consumption.sh` is the gate, wired into
  `scripts/verify.sh` as `context-pack-consumption`.
- The **hint provenance shape** #53's "issue-hint" half needs is already pinned in
  `governance/lessons-sync/hints.json`: every hint carries a `lesson_id`, an `issue`
  reference and a `commit` sha, and a bare string is refused.
- Our own committed triage of this very peer item is
  `governance/sync/peer-board/triages.json` → **`kushin77/deepseek#53`:
  `no-action`**, with the authored reason *"Informational: the peer follows an
  already-landed seam and expects no local change, so there is no half for this repo
  to track."* That disposition is the authoritative local reading of #53 and this
  document does not overturn it.

**Their side.** Publishing the documented, versioned, stable contract itself — the
renderer, its determinism test, and the declared schema id. Only the peer can do
that, and measured today it does not exist: the only mentions of a context pack in
the peer tree are roadmap prose (`roadmap/roadmap.toml`, `docs/ROADMAP.md`) naming
this issue.

**The contract we consume.** None today. The `codeidx.context-pack/v1` seam is a
contract with a **different** peer, and `no-action` records that no local half
exists for #53. Were we to consume a peer-published issue-context pack later, the
interface would be a **declared schema id** on the existing `CONSUMED_CONTRACTS`
register — one that satisfies #53's own "stable and versioned, and a change to it
is a versioned change" criterion — with the unknown-id refusal already in place.
**Stable and versioned: not applicable today; the seam it would need is versioned
by construction.**

**Disposition: `link-only`**, consistent with the committed `no-action` disposition
above. §6 carries a *conditional* direction-issue draft for the case where a human
decides the fleet should consume a peer-published pack; it is **not** filed and not
required.

**Command center CLI.** Unlocks nothing on its own — the console's context pack is
already rendered locally. Its value is a **second, optional grounding source** for
the executor prompt, pluggable at a seam that refuses an unknown schema id instead
of degrading silently.

### 4.4 `kushin77/deepseek#91` — `steer-dispatch --runtime claude`

- **State:** open · **Labels:** `area:fleet`, `status:planned`, `area:integration` ·
  **Milestone:** EPIC-16 — Claude Code CLI ↔ DeepSeek sub-agent integration

**Acceptance criteria (quoted):**

> - `steer-dispatch.sh` (EPIC-12.3 #73) gains a `--runtime claude` path:
>   `steer-dispatch run --task "<desc>" --agent <sme> --runtime claude [--async]` spawns
>   `claude --bg --agent <sme> --model <tier-model>` with the #88/#89 environment applied,
>   returning the same JSON task handle.
> - The existing offline tests are extended: `--dry-run` prints the `claude` invocation and the
>   exact argv is asserted, so the wiring cannot silently drift.
> - Depends on #73 (extends its file — that lane is delivered, so this is a sequential
>   follow-up, not a concurrent collision).

**Our side.** The behaviour #91 adds on the peer side **already exists here**, over a
different entry point: `fleet/terminal.py` `build_command()` composes
`shlex.split(runner) + [prompt]`, `run --runner` defaults to `claude -p` (the shipped
Claude CLI headless form), and `--dry-run` prints the exact argv
(`DRY-RUN: <argv>`) without spawning. The directive the run carries comes from the
gated vocabulary in [`fleet/CONTRACT.md`](../fleet/CONTRACT.md) — six verbs, a role
chain, and a FinOps allowlist that refuses anything outside
`tier ∈ {pro, flash}` / `thinking ∈ {none, low, high}`. So this is a **sibling
implementation**, not a consumer of theirs.

**Their side.** The `--runtime claude` branch in `scripts/fleet/steer-dispatch.sh`
and the argv assertion. Measured in the local clone: the script's `run` parser today
accepts `--task`, `--agent`, `--async` and `--dry-run` only — **there is no
`--runtime` flag** — and it selects its binary by probing for `copilot` then `claude`.

**The contract we consume.** None. Our dispatcher never invokes the peer's
`steer-dispatch.sh`; the two are parallel entry points into the same runner form.
The peer's `--dry-run` JSON task handle is stable *by their own test*, but nothing
here depends on it.

**Disposition: `link-only`.** We cite #91 as the peer's local equivalent of a seam
we already own. No dependency, no direction issue — the ask is entirely inside their
tree and already stands on their board.

**Command center CLI.** Unlocks a **cross-check**: the operator can diff the argv
our loop would build against the argv their `--dry-run` prints, so the two
dispatchers' model/agent plumbing cannot silently drift apart without anyone
noticing.

### 4.5 `kushin77/deepseek#118` — Lessons-sync direction issue (from our #424)

- **State:** open · **Labels:** *(none)* · **Milestone:** none
- **Title (quoted):** *"Lessons-sync: agent-orchestrator owns the authoritative ledger;
  declare your derived view + dispatch-hint provenance (from kushin77/agent-orchestrator#424)"*

**Acceptance criteria as written.** A direction issue carries a request rather than a
checklist; its asks are quoted verbatim from the body:

> 1. A stable, addressable lessons-ledger location (or the issue that will host it)
>    so our declared `ledger_ref` resolves — see your #84 (auto-file lessons as
>    GitHub issues and keep the mandated board in sync).
> 2. The dispatch-hint transport named in your #79 (record lessons in the CMR
>    ledger and feed them back as dispatch hints), mapped to the provenance shape
>    we enforce: every hint carries the lesson id, an issue reference, and a commit
>    sha — never a bare string.
> 3. A declared mirror of our lesson ids, so a lesson recorded on our side is
>    discoverable on yours.

A second quoted commitment bounds it:

> **What we will not do.** We do not edit your repository, do not push to it, and
> do not close anything on this board.

**Our side.** Fully specified and already declared:

- The authoritative ledger is `governance/lessons/ledger.jsonl`, documented in
  [`governance/lessons/README.md`](../governance/lessons/README.md) — *"the"*
  record, with four kinds (`incident`, `rca`, `corrective-action`, `lesson`) and a
  gate that refuses a missing reference, an unevidenced lesson, and an empty ledger.
- The declared relationship is `governance/lessons-sync/contract.json`: role
  `writer` here, `derived-view` there, sync **one-way**, and a `ledger_ref` naming
  the peer's **#84** with the honest state `"requested"` — the peer ledger has not
  materialised, so it is absent *by measurement, not by omission*.
- The hint provenance shape is `governance/lessons-sync/hints.json`: three hints,
  each with `lesson_id` + `issue` + `commit`, and a peer reference on the one that
  has a peer counterpart.
- The gate is `scripts/check-cross-repo-lessons.sh`, and the contract text is
  [`CROSS-REPO-LESSONS-SYNC.md`](CROSS-REPO-LESSONS-SYNC.md).

**Their side.** Declaring the derived view, publishing an addressable ledger
location, and mirroring our lesson ids. Measured today: the peer tree has no
lessons ledger and no `docs/operations/lessons.md`, so `peer_lessons` is empty by
measurement — exactly as our contract records it.

**The contract we consume.** `governance/lessons-sync/contract.json` plus
`hints.json`. **Stable: yes** — both are pinned, committed and gate-checked, and the
gate is deterministic and offline by construction (stdlib only, one read per pinned
input, no network, no `gh`, no wall clock). **Versioned: no version string** — it is
a declared relationship between two named roles, not a wire schema. Recording that
honestly matters more than asserting a version that does not exist.

**Disposition: `consume`.** The peer half must land before the declared reference
resolves; the direction issue that requests it **already exists** (#118, filed from
our #424, recorded in [`CROSS-REPO-LESSONS-SYNC.md`](CROSS-REPO-LESSONS-SYNC.md)
§7). Nothing further should be filed.

**Command center CLI.** Unlocks **dispatch hints that carry provenance**: the
console can show, for a dispatched lane, which lesson ids were fed into its prompt
and the commit each was earned by — instead of a bare string that no gate can check.

### 4.6 `kushin77/deepseek#119` — Direction: emit the fleet's frozen metering/budget record shape

- **State:** open · **Labels:** *(none)* · **Milestone:** none
- **Title (quoted):** *"Direction: emit the fleet's frozen metering/budget record shape
  (field list) — from kushin77/agent-orchestrator#425"*

**Acceptance criteria as written.** The body's ask, quoted:

> Please emit your usage/cost/budget export rows in the frozen shape: four groups
> `identity` / `quantity` / `receipt` / `attribution` plus a `claims[]` array.

and its bounded scope, quoted:

> This is a request for a change **on your board**; the fleet will not edit your
> repo. No fleet issue here is closed by this report — it records the field list we
> need so the two sides converge on one shape.

It carries a 22-row canonical-to-peer field map plus **13 fields the fleet emits that
the peer export does not yet carry** (`schemaVersion`, `kind`, `identity.recordId`,
`identity.sourceType`, `identity.sourceKey`, `quantity.costSource`, `quantity.metered`,
`quantity.cacheHit`, `quantity.unmeteredReason`, `receipt.evidence`,
`attribution.route`, `attribution.taskClass`, `claims`), and a truthfulness rule: a
claim whose `evidence` is empty is refused.

**Our side.** Complete and frozen, before the peer's receipt could harden into a
third variant:

| Surface | What it fixes |
|---|---|
| `docs/contracts/metering/metering-record.schema.json` | the record shape — four groups plus `claims[]`, closed (`additionalProperties: false`) |
| `docs/contracts/metering/field-map.json` | the fleet ↔ peer map, with a **`one_sided`** marker so a field that exists on one side only is *named*, never silently dropped |
| `docs/contracts/metering/ownership.json` | the **one-writer** map — the single producer of every field, `peer-deepseek-export` included |
| `docs/contracts/metering/*.example.json` | two committed instances (the fleet side and the peer export mapped onto the shape) the gate validates |
| `scripts/check-metering-parity.sh` | the gate: offline, deterministic, tri-state, and it proves it can fail (unmapped field, no producer, empty-evidence claim, absent input) |

The rationale is in [`contracts/metering/README.md`](contracts/metering/README.md).

**Their side.** Emitting rows in that shape, naming a producer per field, adding the
13 missing fields, and never asserting a claim it cannot measure. Their own #55
(align the telemetry export with the fleet's metering schema) and #115 (the record
shape exists but nothing produces it) are the same work from their side.

**The contract we consume.** `metering-record.schema.json` — **stable and
versioned**: the shape carries `schemaVersion` and `kind`, the schema is closed, the
field map names every one-sided field, and the ownership map refuses a field with two
producers (half-coupling) or none (unowned). This is the strongest contract in this
document.

**Disposition: `consume`.** The direction issue that requests the peer half **already
exists** (#119, filed from our #425, recorded in
[`contracts/metering/README.md`](contracts/metering/README.md)). Nothing further
should be filed.

**Command center CLI.** Unlocks a **budget rail the console can actually reconcile**:
our budget panel and the peer's export describe spend with one vocabulary, so an
unpriced row shows as unpriced on both sides and a claim reaches the operator with
its evidence attached.

## 5. Others on the open board that bear on remote control or dispatch

These are **not** among the six named items, but they bear on the same three seams
and are recorded so the mapping is not read as complete without them.

| Issue | Title | Why it bears on remote control / dispatch | Disposition |
|---|---|---|---|
| `kushin77/deepseek#1` | Declare as CMR sub-module of agent-orchestrator (module.json + parent declaration) | The one peer item that makes the **relationship itself** explicit: `governance.parent_module: "agent-orchestrator"` in their `module.json`. A parent declaration is the machine-readable form of "the peer consumes/extends this repo", and this repo ships a `module.json` of its own. It is also the only item whose subject is *our* repo | `consume` — the declaration is theirs; the parent side is a `module.json` / CMR-module contract, not a runtime interface |
| `kushin77/deepseek#90` | Declare the Claude-CLI sub-agent capability in module + CMR | The item that would let **our** dispatch discover their executor as a capability: a `claude-cli-subagent` capability in their `module.json` + `config/tiers.toml`. That is the plug-in point for `fleet/routing.py`, whose whole job is resolving a capability to a persona and a FinOps block from a declared policy plus registry cards | `consume` — our `fleet/routing.py` capability resolver would read a declared capability; their #90 is the ask that creates it |
| `kushin77/deepseek#89` | Route Claude CLI sub-agents to DeepSeek: dedicated or optional per-SME | Per-SME model routing is an input to our SME routing contract (`docs/SME-ROUTING.md`, `gateway/sme-routing/policies/`). The peer's L0/L1/L2 → `deepseek-v4-flash` / `deepseek-chat` / `deepseek-reasoner` mapping is a tier vocabulary we would have to reconcile, not invent | `link-only` |
| `kushin77/deepseek#88` | Claude Code CLI to DeepSeek BYOK: main agent on DeepSeek (exact usage) | The env vars (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`) are exactly what our dispatcher injects into a lane's environment (`run_once(..., env=...)`). It names the wiring #91's criterion means by "with the #88/#89 environment applied" | `link-only` |
| `kushin77/deepseek#67` | MCP bridge: register deepseek-agent with Copilot | The MCP resource surface (`deepseek://usage`, `deepseek://budget`, `deepseek://degradation`) is the **pull** half of remote control — a console could read a peer's own state rather than a projection of it. `gateway/mcp/sources.py` already declares a peer authority as a family (`codeidx` → `kushin77/code-indexing`) precisely so the tool *says it cannot reach it* instead of inventing an answer | `link-only` — the family pattern is the seam, but no peer family is declared here today |
| `kushin77/deepseek#55` | Align telemetry export with the fleet's metering schema | The peer's own execution of the shape #119 requests — the sibling of §4.6 from their side | `link-only` (covered by #119's direction) |
| `kushin77/deepseek#54` | Model-profile recognition for the DeepSeek tiers | Maps the fleet's model-profile vocabulary onto their tiers (flash = L0 default, pro = L1+). Our routing contract already names a closed `tier` vocabulary (`flash` / `pro` / `auditor`) in [`contracts/routing/README.md`](contracts/routing/README.md) | `link-only` |
| `kushin77/deepseek#56` | Scope discipline: module here, applications elsewhere | The peer writing down the same NG4 boundary from their side. Worth citing in any future boundary discussion, and the cheapest possible alignment: two repos stating one rule | `link-only` |

## 6. Direction issues

**None must be filed.** The two peer items that need a peer-side change already have
their direction issue on the peer's board, filed by earlier lanes under NG4 and
recorded here in backticks:

| Direction issue | Filed from | Requests |
|---|---|---|
| `kushin77/deepseek#118` | our #424 | the derived lessons view + dispatch-hint provenance (§4.5) |
| `kushin77/deepseek#119` | our #425 | the frozen metering/budget record shape (§4.6) |

Neither is re-filed, edited, or closed here, and this document writes no cross-repo
`Closes` reference.

**The channel, measured.** The documented auto-file path is the CMR direction
channel, whose registry is `vendor/CMR/channels/spokes.tsv` and whose
`direction_labels` column is what gets applied on the target board when a direction
issue is filed. Measured while writing this document: that file carries **35 lines
and no `deepseek` row**, so this peer has **no registered `direction_labels` set**,
and the two precedent direction issues (#118, #119) carry an **empty label array**.
The channel's documented path is therefore **not unambiguous for this peer**, which
is why nothing is filed by this document and why the draft below is presented for a
human decision rather than executed.

**Conditional draft — not filed, not required today.** It exists only for the case
where a human decides the fleet should consume a peer-published issue-context pack
(§4.3), which the committed triage currently dispositions `no-action`:

- **Target repo:** `kushin77/deepseek`
- **Proposed title:** `Direction: publish a versioned issue-context pack contract for
  fleet dispatch (from kushin77/agent-orchestrator)`
- **Proposed labels:** *(none)* — the two precedent direction issues on this board
  carry no labels, and `spokes.tsv` registers no `direction_labels` row for this
  peer; inventing a label would assert a channel registration that does not exist.
- **Proposed body:**

  > Direction issue opened by the fleet per the cross-repo execution boundary
  > (NG4): a peer's request is a direction issue on the peer's board, never an edit
  > from `kushin77/agent-orchestrator`.
  >
  > ## Context
  >
  > `kushin77/agent-orchestrator` already renders a context pack offline for each
  > dispatched lane (`fleet/terminal.py`: issue title, body, the issue's own
  > `Verify:` clause, and matching ledger lessons, resolved from the committed board
  > snapshot with a named warning when a field is missing), and it already consumes
  > one peer-published pack contract as opaque bytes —
  > `codeidx.context-pack/v1` from `kushin77/code-indexing` — refusing an unknown
  > declared schema id by name. Your `#53` asks for the same contract from your side.
  >
  > ## The ask
  >
  > Publish a **declared, versioned** schema id and a deterministic offline renderer
  > for an issue context block, so a consumer can:
  >
  > 1. name the schema id it consumes, and refuse an unknown one by name rather than
  >    consume it under a shape it may not match;
  > 2. obtain the same block twice and get byte-identical output for the same input;
  > 3. read it without network access at build or gate time.
  >
  > State the id (for example `deepseek.issue-context-pack/v1`) and the exact field
  > list in your `#53`, and record it as the contract so a change to it is a
  > versioned change.
  >
  > ## Scope note
  >
  > This is a request for a change **on your board**; the fleet will not edit your
  > repo, and no issue here is closed by this report. It exists so the two sides can
  > converge on one declared id rather than a shape guessed from prose.

## 7. Disposition summary

| # | Title (short) | Disposition | One-line reason |
|---|---|---|---|
| 30 | Expose usage and cost through CLI and MCP resources | `consume` | we already freeze the honesty semantics (`costComplete=False`, unknown → `None`, never 0) and the record shape; the peer's CLI/MCP half is already requested by their #119 |
| 52 | DS worker-tier integration with read-only default | `consume` | its "auditable ledger record naming tenant, agent, tier, model" *is* our frozen metering record; the read-only default is already ADR'd on their side |
| 53 | Dispatch integration: context pack and issue-hint consumption | `link-only` | our committed triage dispositions it `no-action`; our own pack renders locally and the peer-published contract does not exist yet |
| 91 | `steer-dispatch --runtime claude` | `link-only` | our dispatcher already builds the `claude -p` argv with an asserted `--dry-run`; the peer's script is a sibling, not a dependency |
| 118 | Lessons-sync direction issue (from our #424) | `consume` | the authoritative ledger, the derived-view declaration and the hint provenance shape are ours and pinned; the peer half must land and is already requested |
| 119 | Direction: emit the frozen metering/budget record shape | `consume` | the shape, a closed schema with `schemaVersion` + `kind`, an `one_sided`-aware field map and a one-writer ownership map are frozen and gated here; the peer half is already requested |

**No item is `direction-issue-needed` and no item is `blocked`.** The two peer-side
changes that are genuinely required already have their direction issues on the peer
board (#118, #119), and every other mapping is either a contract we already own or a
citation.

## 8. What could not be evidenced

Recorded so this document is not read as stronger than it is:

- **The peer board's live state is a snapshot.** Every peer issue quoted here was
  read with a read-only `gh api repos/kushin77/deepseek/issues/<n>` call and the
  open list with `gh issue list --state open --limit 300`. Issue text can change
  after this document lands; the quoted acceptance criteria are as of
  **2026-09-14**.
- **"Stable and versioned" is asserted only where a version marker exists.** For
  §4.5 the contract is pinned and gated but carries **no version string**; that is
  stated rather than papered over with a version that does not exist.
- **The peer's `usage --json` and `deepseek://usage` payloads are not treated as a
  contract.** Their stability is a deliverable of #30, not a property they have.
- **The local clone is a point-in-time checkout**, head `b229b23`, used to measure
  peer-side state (the absence of a `--runtime` flag, the absence of a lessons
  ledger, the shape of `RESOURCE_SPECS`). The board, not the clone, is authoritative
  for what the peer plans.
- **No peer-side behaviour was executed here.** Nothing in the peer tree was run,
  imported, or written — the clone was read only, which is the boundary.
- **`vendor/CMR/channels/inbox.tsv` was read from the shared checkout, not this
  worktree**, because this worktree's `vendor/` is unpopulated. The deepseek-absence
  finding in §6 is from `spokes.tsv`, a committed registry file, not from the inbox
  ledger.

## 9. Related

- [`CROSS-REPO-EXECUTION-BOUNDARY.md`](CROSS-REPO-EXECUTION-BOUNDARY.md) — the NG4 contract every mapping obeys.
- [`CROSS-REPO-SYNC-OWNER.md`](CROSS-REPO-SYNC-OWNER.md) — the standing peer-board triage that surfaces and dispositions these items.
- [`CROSS-REPO-LESSONS-SYNC.md`](CROSS-REPO-LESSONS-SYNC.md) — the lessons-loop sibling contract, and the record of direction issue #118.
- [`contracts/metering/README.md`](contracts/metering/README.md) — the frozen metering/budget record shape, and the record of direction issue #119.
- [`contracts/routing/README.md`](contracts/routing/README.md) — the routing seam, whose `tier` vocabulary the peer-side tier issues would have to reconcile with.
- [`fleet/CONTRACT.md`](../fleet/CONTRACT.md) — the normative fleet contract the command center CLI enforces.
- `governance/sync/peer-board/triages.json` — the authored disposition for `kushin77/deepseek#53` (`no-action`) this document does not overturn.
- `kushin77/deepseek#30`, `#52`, `#53`, `#91`, `#118`, `#119`, `#1`, `#54`, `#55`, `#56`, `#67`, `#88`, `#89`, `#90`; `kushin77/deepseek#84`, `#79` (the peer lessons loop).
