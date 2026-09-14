# The diagrams capability register — what "fully capable" means for the fleet

Issue: [#467](https://github.com/kushin77/agent-orchestrator/issues/467) ·
Parent EPIC: [#462](https://github.com/kushin77/agent-orchestrator/issues/462) ·
The integration that consumes the module: [#461](https://github.com/kushin77/agent-orchestrator/issues/461) ·
Vendor board read: **2026-09-14** · Register revision: **1**

## Why this document exists

`kushin77/diagrams` is a **GR-18 mandatory** CMR module (`mandatory: true`,
`mandatory_consumer_assets: ["gdc-manifest.yaml","architecture.yaml"]`) and this
repo is now a declared member of its SSOT network. A mandatory dependency whose
capability set is *assumed* is a liability, so this register states — capability
by capability — **what the fleet needs from the module and how we know we have
it**, with every row grounded against the vendor's own board rather than against
our wish list.

It is the same discipline as the sibling gap analysis
[`PAPERCLIP-ING-GAP-ANALYSIS.md`](PAPERCLIP-ING-GAP-ANALYSIS.md): cite what
exists, name what does not, file only the difference. It is deliberately **not a
roadmap** — nothing here asks the fleet to build a renderer, and nothing here
attributes an intention to the vendor that the vendor has not written down.

## Conventions — read this before the table

1. **Every bare `#N` in this document is an issue on `kushin77/diagrams`** (the
   vendor board). References to our own board are always written as a full URL
   or prefixed `kushin77/agent-orchestrator#N`; references to the hub are
   prefixed `kushin77/CMR`. This matters because this repo, the hub and the
   vendor each have an issue `#243` that means something different.
2. **`ref` is one vendor issue URL, and nothing leads it.** The canonical cell
   shape is `<https://github.com/kushin77/diagrams/issues/N>` optionally
   followed by ` — ` and the evidence clause:
   `completion signal: …` for a `shipped` row, `state 2026-09-14: …` for an
   `in-flight` or `UNVERIFIED` row. The literal token `GAP` is the value used
   when no direction has been filed; **no row in this revision uses it**,
   because every gap below already has a filed direction issue whose URL is the
   `ref` (see "A `GAP` row is only legal with a filed direction" below).
3. **Status is a claim about the capability, not about the issue's state.** The
   four values are `shipped`, `in-flight`, `gap`, `UNVERIFIED`, and:
   - `shipped` requires a **completion signal** — a comment id, commit sha or
     tag ref that a reader can re-fetch and re-read. The vendor's own
     `GOVERNANCE.md` rule 10 ("a merged PR is not acceptance-criteria evidence,
     'verified correct' and 'verified landed' are two different checks") is the
     bar being applied.
   - **A closed issue with no completion signal is `UNVERIFIED`, never
     `shipped`.** That is the whole point of the column: `#374` and `#371` below
     are both closed, and neither is evidence that the capability exists.
   - `gap` means we have established the capability is absent (or not
     consumable), and the `ref` is the **direction issue** that names the gap.
   - `in-flight` means vendor work is open and tracked to a signal.
4. **`owner` is never blank** and is one of `kushin77/diagrams`, `us`, `both`.
   `both` is used where the capability only exists as an interface between the
   two halves — our declaration and their ingest, our adapter and their fixture.
5. **`acceptance` must be able to fail**, phrased as something observed (an
   exit code, a value at a path, a state read from the API), and `verify` is the
   command that observes it. Where the observation is a vendor-board read the
   command is a `gh api` call; where it is ours it is a gate in this repo.
6. **Scope of the register**: every capability the integration EPIC
   ([#461](https://github.com/kushin77/agent-orchestrator/issues/461)) relies on,
   plus the fleet-direction set its parent EPIC
   ([#462](https://github.com/kushin77/agent-orchestrator/issues/462)) filed.
   Capabilities that exist in the module but that we do not consume —
   `plan-preview`, `mcp-kb` — are **deliberately out of scope** and are named as
   such at the end rather than padded into the table.

### How the future enforcement gate reads this file

The sibling issue [#469](https://github.com/kushin77/agent-orchestrator/issues/469)
adds `scripts/check-diagrams-capability-register.sh`. This file is written to
survive it: one table, the seven columns, the four statuses, the three owners,
and these two properties it must fail by name on:

- a row marked `shipped` whose `ref` carries no `completion signal:` clause;
- a row marked `gap` whose `ref` is not a filed direction-issue URL (any `GAP`
  row must record the direction it was filed as, in the same cell).

Because every capability in the table is a capability of the *module*, every
`ref` in the table points at the vendor board. Our own half of each capability
is named in the row notes and in "Our half of the contract" rather than given a
row of its own — a row about our own work would make the `owner` column lie.

## The register

| capability | needed-for | owner | ref | status | acceptance | verify |
|---|---|---|---|---|---|---|
| C1 · a per-repo declaration the module's sync can ingest: `architecture.yaml` (`schema: cmr.architecture-manifest/v1`) plus the `gdc-manifest.yaml` `diagrams.blueprint` pin held to the module's declared version | decision-making, task-completion | both | https://github.com/kushin77/diagrams/issues/368 — state 2026-09-14: open, `closed_at` null | in-flight | the module's fleet sync enumerates this repo and rewrites the `generated` block; today `generated.last_synced`, `generated.synced_by` and `generated.content_hash` are all `null` in this repo's own `architecture.yaml`, so the declaration is provably unread and the row fails | `bash scripts/check-diagrams-declaration.sh && gh api repos/kushin77/diagrams/issues/368 --jq .state` |
| C2 · extract a governed repo's real resource inventory (`ssot-extract`), the raw input every other capability is computed from | decision-making, task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/100 — completion signal: comment 5529493429, delivery commit `e00394f` merged and deployed | shipped | a real extraction run returns a non-empty inventory — the module's own live pilot run reported 8,875 real resources; a run that returns zero resources, or a run that cannot name the extractor that produced it, fails | `gh api repos/kushin77/diagrams/issues/100/comments --jq 'any(.[]; .id == 5529493429)'` |
| C3 · a generalized node/edge graph model with overlays (`topology-graph`) that a consumer can reason over, not just look at | decision-making, futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/176 — completion signal: comment 5533799147, independent verification that `TopologyNode`/`TopologyEdge`/`TopologyGraph` exist as described | shipped | the model is importable and exposes a documented node/edge vocabulary a consumer can name; a claim resting only on the feature list in the catalog manifest fails | `gh api repos/kushin77/diagrams/issues/176/comments --jq 'any(.[]; .id == 5533799147)'` |
| C4 · render the extracted topology into a diagram (`blueprint-gen`) | decision-making | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/100 — completion signal: comment 5529493429, auto-generated diagrams from extracted resources shipped and deployed | shipped | a render produces a diagram artifact from extracted state without hand-authoring; a documented feature with no produced artifact fails | `gh api repos/kushin77/diagrams/issues/100/comments --jq 'any(.[]; .id == 5529493429)'` |
| C5 · deterministic drift comparison of live vs declared state surfaced as Findings (`drift-detect`) | decision-making, task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/148 — completion signal: comment 5531349494, `cloud_asset_api/drift.py` + `POST /api/drift/check`, closing with evidence | shipped | a mutated input flips a resource from aligned to drift, and the Finding names the resource, the attribute, the declared and the live value; an implementation that cannot fail on a mutated input fails | `gh api repos/kushin77/diagrams/issues/148/comments --jq 'any(.[]; .id == 5531349494)'` |
| C6 · drift Findings produced on a schedule, with no human triggering the check | decision-making | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/225 — completion signal: comment 5549010977, "verified in code": `_run_drift_check_if_due()` on its own cadence (`DRIFT_CHECK_INTERVAL_SECONDS`, `DRIFT_CHECK_ENABLED`) | shipped | with the scheduler running, a Finding appears for a resource whose live state changed and no API call was made to ask for it; a drift check that only ever runs on request fails | `gh api repos/kushin77/diagrams/issues/225/comments --jq 'any(.[]; .id == 5549010977)'` |
| C7 · a Finding's stable machine shape (resource, attribute, declared value, live value, status) that a stdlib-only consumer can quote as ticket evidence | decision-making, task-completion | both | https://github.com/kushin77/diagrams/issues/128 — completion signal: comment 5533797211, "confirmed complete", live-verified through a real `GET /api/…` read, and named as the backbone of later Finding work | shipped | a consumer parses one Finding with no third-party dependency and can name the two values it is comparing; a shape only observable through the vendor's own UI fails | `gh api repos/kushin77/diagrams/issues/128/comments --jq 'any(.[]; .id == 5533797211)'` |
| C8 · the read surface documented and frozen as a contract (REST + Socket.IO), including the refusal/error shapes a consumer's negative controls need | decision-making, futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/312 — completion signal: comment 5576814350, independent close-out re-run at `origin/main` `5e7aad8`; `docs/reference/openapi.json` 185,705 bytes on that commit | shipped | the frozen contract artifact is present on the vendor's default branch and a consumer can read the endpoint set from it; a contract that exists only in prose fails | `gh api repos/kushin77/diagrams/issues/312/comments --jq 'any(.[]; .id == 5576814350)'` |
| C9 · a versioned-contract mechanism the vendor can point a consumer-read artifact at, so "I pin v0.1" can mean something | futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/310 — completion signal: tag `refs/tags/e2e-harness/v1.0.0` at `79521e76384a6bf8d5af64f9231b9ccc43564fc3`, read from the API (a resolved tag, not a prose claim) | shipped | a version string named by a consumer resolves to a real ref on the vendor's repository; a version that is only a phrase in a document fails | `gh api 'repos/kushin77/diagrams/git/ref/tags/e2e-harness%2Fv1.0.0' --jq .object.sha` |
| C10 · a **versioned** consumer contract for the artifacts we actually read (topology graph, Findings, blueprint output), with a declared rule for what counts as breaking | futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/515 — direction filed 2026-09-14 naming exactly this gap | gap | a consumer can state the contract version it pins and detect that the version changed without reading the changelog; until then the row fails by construction, because the version is not discoverable from the artifact | `gh api repos/kushin77/diagrams/issues/515 --jq .state` |
| C11 · a published offline fixture per contract version, so a consumer tests against the vendor's recorded shape instead of inventing its own | futureproofing, task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/515 — direction filed 2026-09-14; asks for the fixture and the breaking-change rule together | gap | a consumer's own fixture can be diffed against a vendor-published one for the same contract version; today there is nothing to diff against, which is why this row fails | `gh api repos/kushin77/diagrams/issues/515 --jq .state` |
| C12 · a machine-checkable completion signal when the vendor closes an issue we depend on, so `closed` can be told from `shipped` — and from `closed as superseded` | futureproofing | both | https://github.com/kushin77/diagrams/issues/516 — direction filed by this lane on 2026-09-14 (see "Directions filed") | gap | a query over the closed direction set returns at least one signal comment per issue, and a superseded close is distinguishable from a delivered one; today the same query lists issues closed with zero comments, so the row fails | `gh api repos/kushin77/diagrams/issues/516 --jq .state` |
| C13 · a deterministic offline render that emits a `content_hash`, so a diagram can be a gate artifact rather than a portal visit | task-completion, futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/514 — direction filed 2026-09-14 | gap | two runs over the same saved input emit the same hash with no network call, and a mutated input changes it with a non-zero exit naming the difference; a run whose hash comes from a literal fails | `gh api repos/kushin77/diagrams/issues/514 --jq .state` |
| C14 · a first-class, byte-stable **Mermaid export** (today the module documents Mermaid *import*) | task-completion, futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/514 — direction filed 2026-09-14; the manifest's `blueprint-gen` names Mermaid import, not export | gap | a diagram survives into a pull-request or issue body as diffable text without a human cleaning it up, byte-identical across runs; an export that is not byte-stable fails | `gh api repos/kushin77/diagrams/issues/514 --jq .state` |
| C15 · the decision carried alongside the state: an opaque annotation on nodes and edges that round-trips through both the rendered artifact and the machine surface | decision-making | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/513 — direction filed 2026-09-14 | gap | set an annotation, render, read it back from the machine output, on the same node identity; an annotation that survives the render but not the API fails, and an opaque value the module interprets fails worse | `gh api repos/kushin77/diagrams/issues/513 --jq .state` |
| C16 · a *supplied* (non-extracted) graph rendered by the same engine under a declared input contract — e.g. a ticket dependency DAG — so state and plan appear in one vocabulary | decision-making | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/513 — direction filed 2026-09-14; the one shipped instance is the CMR catalog feed (see row notes) | gap | a graph that no extraction produced renders through the same node/edge vocabulary, with the input contract written down and a malformed graph producing a real non-zero exit; an undocumented one-off path fails | `gh api repos/kushin77/diagrams/issues/513 --jq .state` |
| C17 · blueprint as a verified artifact: drift yields an exit code a gate can act on | task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/369 — completion signal: comment 5576932703, independently re-run on `origin/main`; ships `cloud_asset_api/blueprint_verify.py` and a documented contract | shipped | the verifier exits non-zero on a drifted manifest and zero on a conformant one, and an unreadable input never exits zero; a verifier that cannot fail fails | `gh api repos/kushin77/diagrams/issues/369/comments --jq 'any(.[]; .id == 5576932703)'` |
| C18 · blueprint diff rendering plus drift → direction-issue automation, so a drift can turn into a trackable task without a human transcribing it | task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/375 — completion signal: comment 5608669734, all three acceptance criteria met, `POST /api/diagrams/diff/blueprint` for any two refs | shipped | a diff between two refs returns renderable added/removed/changed nodes and the draft-issue path names the routed owner; a diff that cannot show a changed node fails | `gh api repos/kushin77/diagrams/issues/375/comments --jq 'any(.[]; .id == 5608669734)'` |
| C19 · Findings and distributed issues carry `cmr-refs` cross-reference markers, so a Finding can be traced back into our own issue text | decision-making, task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/372 — completion signal: comment 5576943310, markers re-generated and graded by CMR's own vocabulary check at `origin/main` `5e7aad8` | shipped | a generated marker passes the vocabulary validator and appears on the Finding; a marker set that the validator refuses fails | `gh api repos/kushin77/diagrams/issues/372/comments --jq 'any(.[]; .id == 5576943310)'` |
| C20 · an agent-facing read API for the module's own blueprint, usable without a browser session | decision-making, task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/374 — completion signal: comment 5610698989, acceptance criteria 2 and 3 shipped and independently re-verified (PR #498, commits `71bf76e`/`8dfef0a`, 19/19 tests), with the single-repo form `GET /api/blueprints/kushin77/diagrams` recorded as done and tested | shipped | a token-authenticated caller fetches a blueprint without a portal session, and the same call for a repository the route is not scoped to is refused rather than silently returning another repo's data | `gh api repos/kushin77/diagrams/issues/374/comments --jq 'any(.[]; .id == 5610698989)'` |
| C21 · that same read generalized to **any** governed repo, which is what lets our operator surface read *our* blueprint from the module | decision-making, task-completion | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/374 — state 2026-09-14: **closed** 2026-09-10, and its own close-out comment names acceptance criterion 1 as unchecked; no completion signal for the generalized route | UNVERIFIED | a caller fetches a blueprint for a repo other than the module's own; the temptation is to mark this `shipped` because `#374` is closed, and the row is `UNVERIFIED` precisely because closure is not evidence | `gh api repos/kushin77/diagrams/issues/374 --jq '{state,closed_at}'` |
| C22 · architecture-cost attribution on the blueprint (FinOps for the live state) | decision-making | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/373 — state 2026-09-14: open, `closed_at` null; its phase 1 shipped (`84e3c17`) with live pilot evidence, phase 2 blocked by `#368`, and it carries no completion signal | in-flight | a costed resource graph is returned for a governed repo and the cost of a node is traceable to a priced resource; a cost overlay available only for the vendor's own pilot fails | `gh api repos/kushin77/diagrams/issues/373 --jq '.state, .closed_at'` |
| C23 · reverse-direction codegen from a tagged diagram to real Terraform HCL (`blueprint-engine`), consumed PR-only and flag-gated OFF if ever used | futureproofing | kushin77/diagrams | https://github.com/kushin77/diagrams/issues/147 — completion signal: comment 5531345325, closing with evidence for `cloud_asset_api/blueprint_engine.py` and its API route | shipped | the generator produces HCL only from shapes tagged with a resource type and refuses untagged input, naming what it refused; a generator that infers a resource type fails | `gh api repos/kushin77/diagrams/issues/147/comments --jq 'any(.[]; .id == 5531345325)'` |

## Row notes — the measured facts behind each status

The vendor's own catalog manifest, read 2026-09-14 from this checkout at
`vendor/CMR/catalog/modules/diagrams/module.json`, declares
`mandatory: true`, `mandatory_consumer_assets: ["gdc-manifest.yaml","architecture.yaml"]`,
`versions.latest: v0.1.0`, and these features: `ssot-extract`, `blueprint-gen`,
`blueprint-engine`, `drift-detect`, `plan-preview`, `topology-graph`,
`rest-api`, `portal-web`, `vendor-adapters`, `mechanical-checks`, and `mcp-kb`
(`default: off`). A feature listed there is a **declaration**, not a completion
signal — which is why no row below is `shipped` on the strength of the manifest
alone.

### Declaration and ingest (C1)

- The hub publishes the declaration contract at
  `catalog/schemas/architecture-manifest.schema.json` (`$id`
  `urn:cmr:catalog:architecture-manifest.schema:v1`, required fields `schema`,
  `repo`, `generator`, `live_resources`, `terraform_state`, `generated`;
  `schema` fixed to `cmr.architecture-manifest/v1`). Read from `kushin77/CMR`
  through the API on 2026-09-14; the local gate validates against the vendored
  copy at `vendor/CMR/catalog/schemas/architecture-manifest.schema.json`.
- This repo's own declaration is `architecture.yaml` and `gdc-manifest.yaml` at
  the root, both landed by
  [kushin77/agent-orchestrator#464](https://github.com/kushin77/agent-orchestrator/issues/464),
  both gated by `scripts/check-diagrams-declaration.sh`. Our `gdc-manifest.yaml`
  carries the `diagrams.blueprint` pin at `version: "~0.1"`, `updates: pr`.
- The vendor's own `architecture.yaml` (read via the API) shows the same
  unpopulated block: `generated.last_synced`, `generated.synced_by`,
  `generated.content_hash` are all `null`. The sync that would fill them is the
  one `#368` tracks; its own status comment (2026-09-09) reports the fleet
  dry-run enumerating all governed repos and skipping every one because none but
  the module itself declares an `architecture.yaml` yet. That is the dependency,
  measured, not assumed.

### Extraction, model and render (C2, C3, C4)

- C2 is corroborated beyond `#100`: `#373`'s live run comment (2026-09-10) shows
  the extractor reading 8,875 real resources from a pilot project. The feature
  description names `cloud_asset_api/extractors.py` (`OrgExtractor`) as the
  extractor — the module names its own implementation, which is what makes this
  row checkable rather than aspirational.
- C3's signal is an independent verification comment (`#176`, 2026-09-04)
  confirming `TopologyNode`/`TopologyEdge`/`TopologyGraph` in
  `cloud_asset_api/topology.py`, not a delivery claim by the author.
- C4 cites the delivered auto-generated-diagram half of `#100`; the *export*
  half of rendering is where C14 finds a gap.

### Drift and Findings (C5, C6, C7)

- C5's signal names the comparator and the route
  (`POST /api/drift/check`) and the model it reuses (`#128`'s `Finding`).
- C6 exists because a Finding that only appears when someone asks is not a
  signal our operator surface can rely on. `#225` was closed on a code-level
  verification naming the scheduler hook and its environment gates.
- C7 is the row our adapter's shape depends on. `integrations/paperclip/diagrams.py`
  reads the keys `resource`, `attribute`, `declared`, `live`, `status` for each
  Finding and emits the frozen v2 receipt `{kind, ref, result, checks}` that
  [ADR-0017](decision-records/ADR-0017-diagrams-authority-on-the-operator-surface.md)
  fixed. **Those keys are asserted by our own fixture, not read from a published
  vendor contract** — see C11 below. The rule the adapter enforces — a Finding
  whose declared and live values are equal is *never* drift — is our negative
  control, not a vendor guarantee.

### Contracts and fixtures (C8, C9, C10, C11, C12)

- C8's signal is an independent close-out re-run by a second agent at
  `origin/main` `5e7aad8`, with the frozen artifacts' sizes read back from git
  (`docs/reference/openapi.json`, 185,705 bytes). The vendor's published
  contract set also includes `docs/reference/API_CONTRACT.md`, which documents
  `GET /api/findings` and its keyset ordering.
- C9 is the mechanism row: the vendor already knows how to publish a *versioned*
  contract — the `e2e-harness/v1.0.0` tag resolves to a real object — so C10's
  gap is about applying an existing mechanism to the artifact set a consumer
  reads, not about inventing one.
- C10 and C11 are the two halves of `#515`. The local consequence is concrete:
  `integrations/paperclip/tests/fixtures/diagrams.json` is a **consumer-invented
  recording**, and `integrations/paperclip/diagrams.py` is built against it.
  When the vendor publishes a fixture for a contract version, that file becomes
  diffable; until then our integration is only as right as our guess.
- C12 is the row this lane filed a direction for (see "Directions filed"),
  because three closed issues on the vendor's board carry **no comment at all** —
  `#52`, `#53` and `#67`, all closed 2026-01-26 — and `#371` closed as
  superseded. A register that reads `state: closed` as `shipped` would be wrong
  in four places on that board — and one of them, `#371`, sits inside the
  eight-issue CMR register itself, which is why the register's own rule is that
  a closed issue needs a signal before its capability is called `shipped`.

### The decision layer and the gate artifact (C13, C14, C15, C16)

- C13's local consequence is the sharpest one in this document. Our adapter's
  `diagram-blueprint` receipt uses the blueprint's `content_hash` as its `ref`,
  and today that hash is a **literal written into our own fixture**
  (`sha256:6b1f0c8a…`, `integrations/paperclip/tests/fixtures/diagrams.json`)
  because nothing produces it: `generated.content_hash` is `null` in this repo's
  `architecture.yaml` *and* in the vendor's own. `#514` is the direction that
  would make that value real.
- C14's evidence for the gap is the manifest's own wording — `blueprint-gen`
  names "Graphviz … plus Mermaid import and diagrams.net/drawio (mxGraph)
  export" — with the import half separately delivered under `#290`. Import is
  not export, and neither is byte-stable text.
- C15 and C16 are the two halves of `#513`. C16 is **not** a request for
  something that has never existed: `#370` (closed 2026-09-07 on an independent
  re-verification against the live hub feed, comment 5576938814) shipped exactly
  this for one supplied source — the CMR catalog's `module.json`/`topology.json`
  into blueprint nodes. What `#513` asks for is that path generalized behind a
  declared input contract, which is why the row is `gap` while its instance is
  already proven.
- A caveat the register records rather than hides: `#371` — an external render
  API for another consumer's canvas — was closed as **superseded** on 2026-09-09
  (comment 5609783559: the consumer "solved this independently, and no
  `diagrams`-side work is needed to unblock it"). The lesson for us is that
  consuming the module's renderer is not a promise the vendor has made, which is
  why C4 is scoped to *rendering* and C13/C14 carry the consumption requirement.

### The read surface (C20, C21)

- C20 and C21 are the same issue at two scopes, and they get different statuses
  on purpose. `#374`'s close-out comment (2026-09-10) is unusually honest: it
  reports acceptance criteria 2 and 3 shipped with tests, and states that
  acceptance criterion 1 "stays unchecked" because the parameterized
  `{owner}/{repo}` route remains scoped out and blocked on `#368`. The
  capability we need is the parameterized one — our operator surface reads *our*
  repo — so C21 is `UNVERIFIED`: closed, with no completion signal for the thing
  we rely on. Marking C21 `shipped` because the issue is closed is exactly the
  failure this register exists to prevent.

### Cost and codegen (C22, C23)

- C22's `in-flight` rests on measured intermediate work, not on optimism: phase
  1 (`GET /api/finops/architecture`, commit `84e3c17`) was verified against real
  billing data for a pilot, and phase 2 is tracked as blocked by `#368`.
- C23 is out of the integration's path — nothing in
  [#461](https://github.com/kushin77/agent-orchestrator/issues/461) consumes
  codegen — and it is in the register as `futureproofing` because a
  diagram-to-Terraform path that ever ran un-gated would collide with GR-5.
  `#147` closed with evidence naming both the module and its route.

## Directions filed (and the row each answers)

- **Carry the decision alongside the state** — supplied/dependency-graph
  rendering + opaque decision annotation on nodes and edges:
  [kushin77/diagrams#513](https://github.com/kushin77/diagrams/issues/513),
  answering **C15** and **C16**.
- **A diagram as a gate artifact** — deterministic offline render,
  `content_hash`, first-class Mermaid export:
  [kushin77/diagrams#514](https://github.com/kushin77/diagrams/issues/514),
  answering **C13** and **C14**.
- **A versioned consumer contract, a published offline fixture, and a completion
  signal on `#368`/`#373`**:
  [kushin77/diagrams#515](https://github.com/kushin77/diagrams/issues/515),
  answering **C10** and **C11**.
- **A machine-checkable completion signal on close** — telling `closed` from
  `shipped`, and both from `closed as superseded`:
  [kushin77/diagrams#516](https://github.com/kushin77/diagrams/issues/516),
  answering **C12**.

The first three were filed by this EPIC on 2026-09-14 before this register was
written; the fourth was filed by this lane on 2026-09-14, after searching the
vendor's board (332 issues, all states) for an existing direction and finding
none. Per NG4/NG6 the fleet files direction and the vendor implements; nothing
under `kushin77/diagrams` is edited, vendored or forked from this repo, and no
direction writes a closing keyword.

Per the convention recorded in `#515`, a `shipped` row is flipped only when the
vendor's own `CMR:DONE — <ref>` completion signal lands. The sibling issue
[kushin77/agent-orchestrator#468](https://github.com/kushin77/agent-orchestrator/issues/468)
is the tracker that moves this register's `status` column when the board moves.

## Our half of the contract

These are not rows, because they are not capabilities of the module — but they
are the half of each interface the fleet owes, and they are named here so no
reader mistakes a vendor row for the whole story:

- **The declaration** — `architecture.yaml` and the `gdc-manifest.yaml`
  `diagrams.blueprint` pin, plus `scripts/check-diagrams-declaration.sh`
  ([kushin77/agent-orchestrator#464](https://github.com/kushin77/agent-orchestrator/issues/464)).
- **The projection** — `integrations/paperclip/diagrams.py` over the existing
  seam transport, offline through `FixtureTransport`, with a refusal per refused
  shape, gated by `scripts/check-paperclip-diagrams.sh`
  ([kushin77/agent-orchestrator#465](https://github.com/kushin77/agent-orchestrator/issues/465)).
- **The authority decision** — where a diagram signal lives on a ticket, frozen
  by [ADR-0017](decision-records/ADR-0017-diagrams-authority-on-the-operator-surface.md)
  ([kushin77/agent-orchestrator#463](https://github.com/kushin77/agent-orchestrator/issues/463)).
  ADR-0014 keeps `facets` a closed set and the ticket the single join node; the
  blueprint supplies evidence and never a facet of its own.
- **The gate wiring** — `scripts/verify.sh` carries `paperclip-diagrams` and
  `diagrams-declaration` in its checks array
  ([kushin77/agent-orchestrator#466](https://github.com/kushin77/agent-orchestrator/issues/466)).

A capability the vendor ships is still `gap` for us until our half consumes it;
that is why C1 and C7 are `both`.

## What this register does not claim

- **No capability invented on the vendor's behalf.** Every `shipped` row cites a
  completion signal a reader can re-fetch; no row asserts an intention the vendor
  has not written down. If the vendor closes a direction by naming a ref that
  already ships the capability, the row moves to `shipped` with that ref — that
  offer is the vendor's own, standing in `#513`.
- **No renderer of our own.** We consume the module's renderer; C13 and C14 are
  requests *to the vendor*, not work we intend to do. Building a second renderer
  would be a second authority.
- **Deliberately out of scope**: `plan-preview` (we have no plan-preview
  consumer today) and `mcp-kb` (`default: off` in the manifest, a repo-local dev
  tool for the vendor's own sessions). Neither is consumed by
  [#461](https://github.com/kushin77/agent-orchestrator/issues/461), so neither
  gets a row rather than gets a speculative one. `vendor-adapters`,
  `mechanical-checks`, `portal-web` and `rest-api` are likewise module-internal
  surfaces except where a consumer-read artifact flows through them, which is
  covered by C8.
- **Not a roadmap and not a milestone.** This register is unmilestoned with its
  parent EPIC; it tracks capabilities, not dates.
- **The register is about the module, not about the fleet's own quality bar.**
  Nothing here changes this repo's gate of record; the enforcement gate for this
  document is the sibling issue
  [#469](https://github.com/kushin77/agent-orchestrator/issues/469).

## Provenance (GR-10)

- **Module**: `kushin77/diagrams` — CMR catalog manifest
  `vendor/CMR/catalog/modules/diagrams/module.json` (`mandatory: true`,
  `versions.latest v0.1.0`), read 2026-09-14 from this checkout. No vendor code
  is copied, vendored or forked from this repo; direction goes out on the
  vendor's board (NG4/NG6).
- **Vendor board**: read 2026-09-14 through the REST API — 332 issues, all
  states; the CMR-issued enhancement register `#368`–`#375`; every completion
  signal cited above was re-read as a comment id, a commit sha or a tag ref, not
  taken from an issue title or a state field.
- **Hub contract**: `kushin77/CMR` `catalog/schemas/architecture-manifest.schema.json`
  and `catalog/schemas/gdc-manifest.schema.json`, read 2026-09-14 through the
  API.
- **Local measurements**: `architecture.yaml`, `gdc-manifest.yaml`,
  `integrations/paperclip/diagrams.py`, `integrations/paperclip/tests/fixtures/diagrams.json`,
  `scripts/check-diagrams-declaration.sh`, `scripts/check-paperclip-diagrams.sh`,
  `scripts/verify.sh` — all read from this worktree at `origin/master`
  `6bc1463` on 2026-09-14.

## Cross-references

- [PAPERCLIP-ING-GAP-ANALYSIS.md](PAPERCLIP-ING-GAP-ANALYSIS.md) — the house
  pattern for a grounded gap analysis (cite what exists, name what does not).
- [FLEET-DASHBOARD-GAP-ANALYSIS.md](FLEET-DASHBOARD-GAP-ANALYSIS.md) — the
  sibling inventory-style analysis.
- [EXECUTION-PLAN.md](EXECUTION-PLAN.md) — the lane contract this register's
  issue was dispatched under.
- [ADR-0017](decision-records/ADR-0017-diagrams-authority-on-the-operator-surface.md)
  — where a diagram signal lives on a ticket.

## Reconciliation against the vendor board (generated table - do not hand-edit)

Every row above is reconciled against a **recorded read of the vendor board**
(`scripts/fixtures/diagrams-vendor-board.snapshot.json`, recorded 2026-09-14) by
`bash scripts/track-diagrams-capabilities.sh`, which exits `1` and names the class when a
row's declared `status` disagrees with that read. The table below is **generated** from that
tracker's output rather than written by hand; regenerate it with
`bash scripts/track-diagrams-capabilities.sh --emit-reconciliation-table`. The tracker compares
this table against a fresh generation and names `register-section-stale` if the two differ, so
this register cannot age silently. A `finding` verdict is neither a pass nor a failure: it is a
closed vendor issue whose closure is **not** evidence that the capability exists (`#374`),
surfaced by name instead of hidden or called shipped.

| row | vendor issue | register status | vendor state | verdict |
|---|---|---|---|---|
| C1 | [kushin77/diagrams#368](https://github.com/kushin77/diagrams/issues/368) | in-flight | open | consistent |
| C2 | [kushin77/diagrams#100](https://github.com/kushin77/diagrams/issues/100) | shipped | closed | consistent |
| C3 | [kushin77/diagrams#176](https://github.com/kushin77/diagrams/issues/176) | shipped | closed | consistent |
| C4 | [kushin77/diagrams#100](https://github.com/kushin77/diagrams/issues/100) | shipped | closed | consistent |
| C5 | [kushin77/diagrams#148](https://github.com/kushin77/diagrams/issues/148) | shipped | closed | consistent |
| C6 | [kushin77/diagrams#225](https://github.com/kushin77/diagrams/issues/225) | shipped | closed | consistent |
| C7 | [kushin77/diagrams#128](https://github.com/kushin77/diagrams/issues/128) | shipped | closed | consistent |
| C8 | [kushin77/diagrams#312](https://github.com/kushin77/diagrams/issues/312) | shipped | closed | consistent |
| C9 | [kushin77/diagrams#310](https://github.com/kushin77/diagrams/issues/310) | shipped | closed | consistent |
| C10 | [kushin77/diagrams#515](https://github.com/kushin77/diagrams/issues/515) | gap | open | consistent |
| C11 | [kushin77/diagrams#515](https://github.com/kushin77/diagrams/issues/515) | gap | open | consistent |
| C12 | [kushin77/diagrams#516](https://github.com/kushin77/diagrams/issues/516) | gap | open | consistent |
| C13 | [kushin77/diagrams#514](https://github.com/kushin77/diagrams/issues/514) | gap | open | consistent |
| C14 | [kushin77/diagrams#514](https://github.com/kushin77/diagrams/issues/514) | gap | open | consistent |
| C15 | [kushin77/diagrams#513](https://github.com/kushin77/diagrams/issues/513) | gap | open | consistent |
| C16 | [kushin77/diagrams#513](https://github.com/kushin77/diagrams/issues/513) | gap | open | consistent |
| C17 | [kushin77/diagrams#369](https://github.com/kushin77/diagrams/issues/369) | shipped | closed | consistent |
| C18 | [kushin77/diagrams#375](https://github.com/kushin77/diagrams/issues/375) | shipped | closed | consistent |
| C19 | [kushin77/diagrams#372](https://github.com/kushin77/diagrams/issues/372) | shipped | closed | consistent |
| C20 | [kushin77/diagrams#374](https://github.com/kushin77/diagrams/issues/374) | shipped | closed | consistent |
| C21 | [kushin77/diagrams#374](https://github.com/kushin77/diagrams/issues/374) | UNVERIFIED | closed | finding closed-without-signal |
| C22 | [kushin77/diagrams#373](https://github.com/kushin77/diagrams/issues/373) | in-flight | open | consistent |
| C23 | [kushin77/diagrams#147](https://github.com/kushin77/diagrams/issues/147) | shipped | closed | consistent |
