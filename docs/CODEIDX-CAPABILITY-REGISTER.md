# The code-indexing capability register

What the fleet needs from `kushin77/code-indexing`, per capability, and how we know we have it.

Issue: [#478](https://github.com/kushin77/agent-orchestrator/issues/478) ·
EPIC: [#473](https://github.com/kushin77/agent-orchestrator/issues/473) ·
Consumption EPIC this register must cover: [#472](https://github.com/kushin77/agent-orchestrator/issues/472) ·
Vendor board read live: **2026-09-14** (this session)

## Why this register exists

`kushin77/code-indexing` is a GR-18 **mandatory module** and the most mature vendor surface this
repo consumes, and its own board already carries the CMR-issued enhancement register
(`CMR:ENH-IDX-001..012` = vendor `#104`–`#111`, `#126`–`#132`). The honest position is therefore
*not* "add features": it is **finish the contract half**. An open vendor issue is not a shipped
capability, a closed vendor issue is not evidence either, and a capability we merely assume is a
future outage. This document is the register that makes each of those states explicit — one row per
capability, with an owner, a falsifiable acceptance criterion, a real vendor reference, and the
command that checks it.

It is an analysis of the vendor's board, not a wish list: **no capability is recorded without a
reference to a live vendor issue or a filed direction issue**, and no row is `shipped` without a
cited completion signal.

The sibling register for the diagrams module (`docs/DIAGRAMS-CAPABILITY-REGISTER.md`, issue #467)
follows the same row grammar, so the two read as one program.

## How to read a row

Every row in [the register](#the-register) carries exactly these fields:

| Field | Rule |
|---|---|
| `capability` | what the fleet needs, one line, prefixed with this register's row id (`C-nn`) |
| `needed-for` | `decision-making`, `task-completion`, `futureproofing` — one or more |
| `owner` | `kushin77/code-indexing`, `us`, or `both` — never blank |
| `ref` | the issue this row is grounded on, per the grammar below |
| `status` | `shipped`, `in-flight`, `gap`, or `UNVERIFIED` — per the vocabulary below |
| `acceptance` | a criterion that **can fail**, phrased as an observable |
| `verify` | the command that checks it |

### Status vocabulary

| Status | Means |
|---|---|
| `shipped` | The capability exists and a **completion signal** is cited for it. |
| `in-flight` | A vendor issue is open *and* an implementing artifact exists but is unmerged (a pick-up is measured, not hoped for). |
| `gap` | The capability is absent and a direction issue is filed. `gap` without a filed direction is a defect in this register, not a finding about the vendor. |
| `UNVERIFIED` | We already consume the surface, the provider has **not** published the contract, so nothing here can be verified against it. This is the honest state, not a failure. |

### The completion signal (and why "closed" is not one)

A `shipped` row's `ref` cell cites its completion signal in a fixed suffix:

```
ref  := "<link to the issue>" [ " · closed=" <ISO8601> " · signal=" <artifact> ]
```

`closed=` records when the issue closed; **`signal=` names the artifact that proves the capability
exists** — a path in the owner's tree, a commit, a tag, a release, or a measured number. The rule of
this register is that **a closed vendor issue is never itself a completion signal**: "closed" records
that a conversation ended, not that a capability exists. A `shipped` row without a `signal=` clause
is misclassified and belongs in `UNVERIFIED` — saying so is the point of the row.

### The `ref` grammar, and the literal `GAP`

`ref` holds either a real issue link or the literal `GAP` (a gap with no filed direction issue).
**This register contains no `GAP` cell**, and that is deliberate rather than an omission: every row
marked `gap` here has a filed direction issue (which the row's `ref` names), so a `GAP` cell would be
the "`gap` with no filed direction issue" defect by construction. A `GAP` cell is the correct value
only for a gap we have decided not to file — there is none in this register, and filing it here would
be inventing a direction on the vendor's behalf (EPIC #473 forbids that).

A row owned by `us` cites **this repository's** issue, because the artifact that answers the need is
ours; every other row cites the vendor board.

### Reading a bare `#N`

**Every bare `#N` reference in this document is an issue or pull request on the
`kushin77/code-indexing` board** unless the text prefixes it with another repo
(`agent-orchestrator#475`, `CMR#1010`). The two boards' numbers collide — `agent-orchestrator#129`
and `kushin77/code-indexing#129` are unrelated — so a vendor ref is never written as a bare `#N`
outside this register's own rows, and never as a `Blocked-by:` marker.

GitHub numbers *issues and pull requests in one sequence on a board*: `kushin77/code-indexing#140`,
`#143`, `#152` and `#153` are **pull requests**, not issues. Rows below say which is which, because a
tracker that reads a pull request as an issue will report the wrong state.

### What `verify` means when the artifact lives in another repo

Where a row's artifact is inside `kushin77/code-indexing`, `verify` checks the **cited evidence** on
that board (the issue's own closing comment, naming the commit or artifact) rather than pretending to
run the vendor's code from a consumer repo — this repo consumes the published surfaces and does not
vendor their implementation (ADR-0018). Those checks need network access; the rows owned by `us`
verify against offline gates in this checkout. For a `gap` or `in-flight` row, `verify` asserts the
direction issue is **still open**: the check fails the day the vendor closes it, which is exactly when
the row must be re-grounded against the completion signal. That failure is the drift signal, not a
false alarm.

## The register

Rows are grouped by id range; each group is documented in the `capability` column's `C-nn` prefix.

| Id range | Group |
|---|---|
| `C-01`–`C-09` | Shipped vendor surfaces the fleet already consumes |
| `C-10`–`C-25` | The contract half: the absent capabilities and the vendor's open direction set |
| `C-26`–`C-32` | Our half: what this repository must carry to consume the index |

| capability | needed-for | owner | ref | status | acceptance | verify |
|---|---|---|---|---|---|---|
| **C-01** Where is symbol X — a compiler-accurate definition answer (file, range, `fidelity`) for any repo in the org | decision-making, task-completion | kushin77/code-indexing | [#55](https://github.com/kushin77/code-indexing/issues/55) · closed=2026-09-06T21:53:26Z · signal=`codeidx/mcp_server.py` AST tools merged `da1889d`; `tests/test_mcp.py` 13 passed incl. a byte-identical repeat-call golden | shipped | A definition query names the file, range and `fidelity` of an indexed symbol, and two identical queries return byte-identical envelopes | `grep -q da1889d <(gh api repos/kushin77/code-indexing/issues/55/comments --jq '.[-1].body')` |
| **C-02** Semantic coverage for the languages this fleet writes (Python, TypeScript, JavaScript, Go, Java, Rust) plus lexical shell/config definitions | task-completion | kushin77/code-indexing | [#5](https://github.com/kushin77/code-indexing/issues/5) · closed=2026-08-01T13:47:23Z · signal=commit `06d4c7d`; measured javascript 0 to 7 on `ghl-vibe-git-sync`, 483 to 1343 documents on `capital-underwriting` | shipped | The module manifest declares six `lang-*` features, and a registered repo returns a non-zero semantic hit count for a Python symbol and for a TypeScript symbol | `grep -q 06d4c7d <(gh api repos/kushin77/code-indexing/issues/5/comments --jq '.[-1].body')` |
| **C-03** Reproducible answers — no LLM, embeddings or fuzzy matching in the index or query path, with byte-stable canonical serialization | decision-making, futureproofing | kushin77/code-indexing | [#46](https://github.com/kushin77/code-indexing/issues/46) · closed=2026-09-06T20:56:03Z · signal=`canonicalize()` in `codeidx/canonical.py`, commit `ec324c4` merged at `e2af5f1`; 22 tests incl. fresh-interpreter byte-stability | shipped | A canonicalized hit set serializes byte-identically in a fresh interpreter, and the module manifest declares no LLM, embedding or fuzzy step | `grep -q ec324c4 <(gh api repos/kushin77/code-indexing/issues/46/comments --jq '.[-1].body')` |
| **C-04** One index artifact published atomically, dry-run by default, with a schema-versioned output and the `query`/`freshness`/`summary` MCP projection tools | task-completion, futureproofing | kushin77/code-indexing | [#80](https://github.com/kushin77/code-indexing/issues/80) · closed=2026-09-06T23:03:01Z · signal=released `v0.2.0` (`8fb9f59`); `--apply` publishes by atomic swap; `codeidx.index/v1`; `docs/finops.md` | shipped | A publish is a dry run unless `--apply` is passed, and a reader never observes a half-written published index | `grep -q 8fb9f59 <(gh api repos/kushin77/code-indexing/issues/80/comments --jq '.[-1].body')` |
| **C-05** A per-repo staleness probe (HEAD, dirty, upstream, ahead/behind) that reports and never pulls | decision-making | kushin77/code-indexing | [#30](https://github.com/kushin77/code-indexing/issues/30) · closed=2026-08-01T15:56:25Z · signal=`codeidx/freshness.py` at `3335686` — a read-only probe | shipped | The probe reports a tree's HEAD and ahead/behind state and never mutates the working tree it reads | `grep -q freshness.py <(gh api repos/kushin77/code-indexing/issues/30/comments --jq '.[-1].body')` |
| **C-06** Per-repo coverage accounting — a missing answer is attributable to a tree that was not indexed rather than read as "absent" | decision-making | kushin77/code-indexing | [#19](https://github.com/kushin77/code-indexing/issues/19) · closed=2026-08-01T15:22:59Z · signal=`index.db` gains a `files` table (`path`, `language`, `fidelity`, `UNIQUE(path, language)`) in `7e481f1`, counted by `completeness.py` | shipped | A tree that contributed zero indexed files is reported as a coverage gap, not as an empty result | `grep -q 7e481f1 <(gh api repos/kushin77/code-indexing/issues/19/comments --jq '.[-1].body')` |
| **C-07** The corpus is real and includes the indexer's own source (dogfooding), with the schema version recorded | futureproofing | kushin77/code-indexing | [#27](https://github.com/kushin77/code-indexing/issues/27) · closed=2026-08-01T18:03:14Z · signal=`b79597a2` schema v2, producer `b4bf1304b81f4a3b` — the repo is in its own corpus | shipped | The index records its own repository as an indexed tree with a schema version and a producer identity | `grep -q b79597a2 <(gh api repos/kushin77/code-indexing/issues/27/comments --jq '.[-1].body')` |
| **C-08** Admitted as a GR-18 mandatory module with its consumer assets declared | task-completion | kushin77/code-indexing | [#73](https://github.com/kushin77/code-indexing/issues/73) · closed=2026-09-06T23:02:58Z · signal=CMR conformance `CONFORMANT — REGISTERED` at `v0.1.0` (`67f9686`); `catalog/mandatory.tsv` row 1 | shipped | The mandatory registry names `code-indexing` with consumer assets `.mcp.json,gdc-manifest.yaml` and the module manifest declares `mandatory: true` with 13 features | `grep -q v0.1.0 <(gh api repos/kushin77/code-indexing/issues/73/comments --jq '.[-1].body')` |
| **C-09** Embeddable and packageable, so the mandatory module can ship to every repo | futureproofing | kushin77/code-indexing | [#59](https://github.com/kushin77/code-indexing/issues/59) · closed=2026-09-06T21:53:36Z · signal=merged `82997a5`; `codeidx-0.1.0-py3-none-any.whl` installs into a fresh venv | shipped | A wheel builds from the module and installs into a fresh virtualenv without the indexer toolchain | `grep -q 82997a5 <(gh api repos/kushin77/code-indexing/issues/59/comments --jq '.[-1].body')` |
| **C-10** The published MCP tool contract plus a recorded fixture — tool names, request/response schemas, and a fixture a consumer can pin to | task-completion, futureproofing | kushin77/code-indexing | [#161](https://github.com/kushin77/code-indexing/issues/161) | gap | The vendor publishes a versioned tool contract at a stable path and `gateway/mcp/kb.py`'s `CODEIDX_TOOLS` is derived from it; today the shapes exist only as a harvested catalog, which is the recorded root cause of `#129` | `test "$(gh api repos/kushin77/code-indexing/issues/161 --jq .state)" = open` |
| **C-11** Per-repo index freshness as a machine-readable contract, plus an exit-code query mode | decision-making | kushin77/code-indexing | [#162](https://github.com/kushin77/code-indexing/issues/162) | gap | A consumer can ask "is the index current for repo R" and receive a machine-readable per-repo answer with an exit-code mode a gate can branch on; until then this repo's `freshness()` returns declared configuration only (ADR-0018 decision 4) | `test "$(gh api repos/kushin77/code-indexing/issues/162 --jq .state)" = open && bash scripts/check-codeidx-backend.sh` |
| **C-12** Cross-repo query semantics with a resolvability tri-state, so "no references" is never confused with "references not resolvable" | decision-making | kushin77/code-indexing | [#163](https://github.com/kushin77/code-indexing/issues/163) | gap | A `references` query returns resolved / none / unresolvable rather than an empty list, because `lang-shell` references resolve nowhere and an empty list is a false absence | `test "$(gh api repos/kushin77/code-indexing/issues/163 --jq .state)" = open` |
| **C-13** The published consumption contract for `codeidx.context-pack/v1` | task-completion | kushin77/code-indexing | [#128](https://github.com/kushin77/code-indexing/issues/128) | UNVERIFIED | The vendor publishes the pack's field contract; until then this consumer models no field of the pack, consumes the bytes verbatim, and its `CONSUMED_CONTRACTS` row reads `UNVERIFIED` — flipping that row while the contract is open fails the gate by name | `bash scripts/check-context-pack-consumption.sh` |
| **C-14** A producer for per-repo pre-indexed context packs, for dispatch front-loading | task-completion | kushin77/code-indexing | [#106](https://github.com/kushin77/code-indexing/issues/106) | gap | A pack for a given repo can be produced and fetched before a dispatch, so the static prefix is shared instead of re-derived per agent | `test "$(gh api repos/kushin77/code-indexing/issues/106 --jq .state)" = open` |
| **C-15** The L1 cache wired into the MCP query path | futureproofing | kushin77/code-indexing | [#104](https://github.com/kushin77/code-indexing/issues/104) | gap | A repeated MCP query is served from the L1 cache rather than recomputed; the counters ship (`#51`, closed) but the query path does not consult them | `test "$(gh api repos/kushin77/code-indexing/issues/104 --jq .state)" = open` |
| **C-16** Cache-hit telemetry exported for cost-report ingestion | futureproofing | kushin77/code-indexing | [#107](https://github.com/kushin77/code-indexing/issues/107) | gap | Hit-rate and latency counters leave the process and reach CMR's cost report; the counters ship but their metrics wiring was deferred in `#51`'s own close | `test "$(gh api repos/kushin77/code-indexing/issues/107 --jq .state)" = open` |
| **C-17** Per-agent and per-SME query cost metering through the gateway ledger | futureproofing | kushin77/code-indexing | [#108](https://github.com/kushin77/code-indexing/issues/108) | gap | An index query is attributed to the agent that asked, in the same ledger the gateway already writes, so index cost is attributable per SME | `test "$(gh api repos/kushin77/code-indexing/issues/108 --jq .state)" = open` |
| **C-18** One reconciled `codeidx.cache-report/v1` shape plus a defined publish path into CMR's fleet report | futureproofing | kushin77/code-indexing | [#126](https://github.com/kushin77/code-indexing/issues/126) | gap | The cache-report shape is reconciled with the documented consumer and has a defined publish path (`#127`), so two producers cannot publish two shapes under one name | `test "$(gh api repos/kushin77/code-indexing/issues/126 --jq .state)" = open` |
| **C-19** The prompt-cache features declared in the module and GDC manifests | futureproofing | kushin77/code-indexing | [#111](https://github.com/kushin77/code-indexing/issues/111) | gap | The prompt-cache capability appears in the module manifest and the GDC manifest, so a consumer can discover it by declaration rather than by reading prose | `test "$(gh api repos/kushin77/code-indexing/issues/111 --jq .state)" = open` |
| **C-20** The measurement artifact behind the DR-069 cited baselines | decision-making | kushin77/code-indexing | [#132](https://github.com/kushin77/code-indexing/issues/132) | in-flight | The L1/L2 measurement behind the cited baselines is published as an artifact a consumer can re-run; the implementing pull request `#153` is open and unmerged | `test "$(gh api repos/kushin77/code-indexing/issues/153 --jq .state)" = open` |
| **C-21** The module's correctness evidence is machine-gated (testing-suite adoption, coverage, mutation, property-based) | futureproofing | kushin77/code-indexing | [#119](https://github.com/kushin77/code-indexing/issues/119) | in-flight | Coverage and mutation measurement run in the module's own gate rather than by assertion; the implementing pull requests `#143` (coverage) and `#140` (testing-suite) are open and unmerged | `test "$(gh api repos/kushin77/code-indexing/issues/143 --jq .state)" = open` |
| **C-22** Flaky-test, chaos/fault-injection and perf-regression guardrails for the indexing pipeline | futureproofing | kushin77/code-indexing | [#122](https://github.com/kushin77/code-indexing/issues/122) | gap | An intermittent check failure is tracked rather than retried away, and the atomic publish path has a fault-injection and a perf-regression guard (`#123`, `#124`) | `test "$(gh api repos/kushin77/code-indexing/issues/122 --jq .state)" = open` |
| **C-23** The MCP test surface runs unskipped — a skip is a cannot-assess, never a pass | futureproofing | kushin77/code-indexing | [#147](https://github.com/kushin77/code-indexing/issues/147) | gap | The three test modules skipped by the `mcp`/`lsprotocol` incompatibility run and pass, so the MCP surface's own evidence is complete | `test "$(gh api repos/kushin77/code-indexing/issues/147 --jq .state)" = open` |
| **C-24** `code-indexing` is declared in the diagrams SSOT (`architecture.yaml`) instead of a prose-only architecture doc | futureproofing | kushin77/code-indexing | [#134](https://github.com/kushin77/code-indexing/issues/134) | gap | The module appears as a declared node in the diagrams SSOT, so the sibling diagrams register (issue #467) can cite it as a declaration rather than as prose | `test "$(gh api repos/kushin77/code-indexing/issues/134 --jq .state)" = open` |
| **C-25** The mandatory `.mcp.json` seed resolves in a consumer repo | futureproofing, task-completion | kushin77/code-indexing | [CMR#1010](https://github.com/kushin77/CMR/issues/1010) | gap | A seed-faithful `.mcp.json` names an indexer entry point that resolves in the consuming repo, or carries a documented substitution point; today the faithful copy declares `catalog/indexer/mcp_server.py` and this repo has no `catalog/` directory, so a gate can pass while declaring an indexer that cannot start | `test ! -e catalog/indexer/mcp_server.py && test "$(gh api repos/kushin77/CMR/issues/1010 --jq .state)" = open` |
| **C-26** The authority split and the no-re-derivation rule are decided and citable, and institutional facts stay in this repo's catalogue | decision-making, futureproofing | us | [agent-orchestrator#474](https://github.com/kushin77/agent-orchestrator/issues/474) · closed=2026-09-14T14:16:50Z · signal=ADR-0018 `status: accepted`, merged `e6250d3` | shipped | ADR-0018 is accepted and `governance/knowledge/catalog.json` remains the home of the seven institutional kinds (adr, architecture, golden-rules, governance, issue-metadata, pattern-template, policy) while the symbol index answers only code facts | `bash scripts/check-knowledge-index.sh && grep -q 'status: accepted' docs/decision-records/ADR-0018-codeidx-consumption-and-index-authority.md` |
| **C-27** The GR-17 mandatory consumer surface: a root `.mcp.json` byte-identical to the vendored seed, plus the `code-indexing.mcp` pin | task-completion | us | [agent-orchestrator#475](https://github.com/kushin77/agent-orchestrator/issues/475) · closed=2026-09-14T14:17:02Z · signal=merged `7266ea5`; `.mcp.json` sha256 `514e6a5c8e16811835b75f8f260ac7467b9aac7c75cc7a9bf91811fef835d91c`, matching the vendored seed | shipped | The root `.mcp.json` matches the vendored seed byte-for-byte and `gdc-manifest.yaml` carries the `code-indexing.mcp` pin `~0.1` | `bash scripts/check-codeidx-surface.sh` |
| **C-28** The gateway consumes the five published tool shapes and can proxy the real indexer behind a default-OFF flag | task-completion | us | [agent-orchestrator#476](https://github.com/kushin77/agent-orchestrator/issues/476) · closed=2026-09-14T14:35:44Z · signal=merged `aaf8b4d`; `scripts/check-codeidx-backend.sh` | shipped | `CODEIDX_TOOLS` names exactly the five published shapes, the opt-in flag defaults OFF, and every answer envelope names its provenance with a fidelity note accurate for that path | `bash scripts/check-codeidx-backend.sh` |
| **C-29** A live index connection answers — an opted-in tenant is served by the real indexer, not the declared fixture | task-completion | both | [#129](https://github.com/kushin77/code-indexing/issues/129) | in-flight | For an opted-in tenant every answer reports `source=codeidx` with no degradation envelope; today the flag defaults OFF, so every tenant is answered by the declared fixture and the vendor's lesson `#129` stays open | `bash scripts/check-codeidx-backend.sh && test "$(gh api repos/kushin77/code-indexing/issues/129 --jq .state)" = open` |
| **C-30** `assemble_prefix` consumes a pre-fetched context pack as opaque bytes, collapsing the two static regions onto one shared prefix | task-completion | us | [agent-orchestrator#477](https://github.com/kushin77/agent-orchestrator/issues/477) · closed=2026-09-14T14:35:47Z · signal=merged `5ddb30d`; `scripts/check-context-pack-consumption.sh` | shipped | A supplied pack is placed ahead of the locally-derived memory block, consumed verbatim, and two assemblies of the same pack share one byte-identical static prefix | `bash scripts/check-context-pack-consumption.sh` |
| **C-31** The vendor's lesson `#130` is resolved at the source — the static-first/delta-last discipline is consumed, not re-derived | futureproofing | both | [#130](https://github.com/kushin77/code-indexing/issues/130) | in-flight | The pack is consumed at the seam, so the discipline is no longer re-derived per consumer; adapting the *spec* locally remains legitimate (ADR-0018 decision 3) and is not what the lesson named | `bash scripts/check-context-pack-consumption.sh && test "$(gh api repos/kushin77/code-indexing/issues/130 --jq .state)" = open` |
| **C-32** The codeidx gates are wired into the gate of record | task-completion | us | [agent-orchestrator#481](https://github.com/kushin77/agent-orchestrator/issues/481) · closed=2026-09-14T14:41:32Z · signal=merged `6bc1463`; `Makefile` target `codeidx` plus three entries in `scripts/verify.sh` | shipped | `make verify` runs the three codeidx checks — `codeidx-surface`, `codeidx-backend`, `context-pack-consumption` — so removing one is a named failure, not a silent gap | `python3 -c "import sys;s=open('scripts/verify.sh').read();sys.exit(0 if all(k in s for k in ('codeidx-surface','codeidx-backend','context-pack-consumption')) else 1)"` |

## The direction set this EPIC filed, and the row each answers

The three direction issues below were filed on the vendor board by this EPIC (#473) on 2026-09-14.
They are cited above as the `ref` of the row they answer, and each was read live for this register
(all three are open, with no closing comment):

| Direction | Vendor issue | The gap it names | Row it answers |
|---|---|---|---|
| Publish the **MCP tool contract plus a recorded fixture**, not only the context-pack doc | [#161](https://github.com/kushin77/code-indexing/issues/161) | With no citable tool contract, a consumer transcribes shapes and drifts (`#129`) | `C-10` |
| **Per-repo freshness as a machine-readable contract**, plus an exit-code query mode | [#162](https://github.com/kushin77/code-indexing/issues/162) | You cannot gate "this task is done" on an index whose currency for a given repo is unknowable | `C-11` |
| **Cross-repo query semantics plus a resolvability tri-state** | [#163](https://github.com/kushin77/code-indexing/issues/163) | One artifact covers six languages and `lang-shell` references are unresolvable, so "no references" and "references not resolvable" are indistinguishable — a false absence | `C-12` |

One further direction is recorded in this register and was filed from the sibling lane that carries the
mandatory consumer asset (issue #475, PR #492): [CMR#1010](https://github.com/kushin77/CMR/issues/1010)
— the mandatory `.mcp.json` seed declares a CMR-relative indexer path that cannot resolve in a
consumer repo. It is cited as the `ref` of row `C-25`, and it is filed on CMR's board because the seed
template lives in CMR (`templates/module/.mcp.json`), not on the vendor's.

## The consumption EPIC's capabilities, against this register

EPIC #472's children are closed and merged; its capabilities must all appear here. Each is mapped to
the rows that carry it. This mapping is the rule that the future gate checks against:

| Capability #472 relies on | Rows |
|---|---|
| The org-wide code-location SSOT answering "where is X" for the agent surface | `C-01`, `C-28` |
| Compiler-accurate, no-LLM, reproducible answers (the reason to consume it at all) | `C-03` |
| One `index.db` published by atomic symlink swap | `C-04` |
| The MCP tool shapes (`definitions`/`references`/`search`/`query`/`freshness`) as a *contract* | `C-10`, `C-28` |
| Reference lookup that cannot report a false absence | `C-12` |
| Per-repo index freshness | `C-05`, `C-11` |
| The `codeidx.context-pack/v1` consumption contract | `C-13` |
| A pre-fetched context pack consumed by `assemble_prefix` | `C-30` |
| The GR-17 mandatory consumer surface (`.mcp.json`, `gdc-manifest.yaml` pin) | `C-27` |
| The real backend behind a flag-gated OFF seam | `C-28`, `C-29` |
| The two-index authority split and the frozen no-re-derivation rule | `C-26` |
| The two cross-repo lessons naming this repo (`#129`, `#130`) | `C-29`, `C-31` |
| The codeidx gates wired into the gate of record | `C-32` |

Both capabilities the issue names as **currently absent** are present and honestly classified rather
than smoothed over: the MCP tool contract is `C-10` (`gap`, direction `#161`) and per-repo freshness
is `C-11` (`gap`, direction `#162`).

## Measured provenance

Every fact a row cites came from one of the following, read during this session. No capability claim
in this register is uncited.

| What | Where it came from | Measured value |
|---|---|---|
| The module's declared feature set | `vendor/CMR/catalog/modules/code-indexing/module.json` (CMR catalog manifest) | 13 features — `indexer`, `lang-ts-js`, `lang-python`, `lang-go`, `lang-java`, `lang-rust`, `lang-shell`, `cli`, `http-api`, `web-ui`, `mcp`, `lsp`, `secret-scan`; `lsp` default **off**; `mandatory: true`; `mandatory_consumer_assets: [".mcp.json", "gdc-manifest.yaml"]`; latest `v0.1.0` |
| The mandatory-module registry row | `vendor/CMR/catalog/mandatory.tsv` (row 1) | `code-indexing` with consumer assets `.mcp.json,gdc-manifest.yaml`, GR-17/GR-18 pairing |
| The shell-language limitation | the same manifest, `lang-shell` feature | "definitions only, references unresolvable" — the root of row `C-12` |
| The vendor board | `kushin77/code-indexing`, read via REST | 124 issues (30 open, 94 closed); EPIC #473 recorded 121 when it was written earlier the same day. The `CMR:ENH-IDX-*` register (`#104`–`#111`, `#126`–`#132`) is present and open |
| Every `closed=` and `signal=` in the register | the cited issue's own closing comment, read via REST | see each row's `ref` cell |
| This repo's consumer surface | `.mcp.json`, `gdc-manifest.yaml`, `gateway/mcp/kb.py`, `engine/memory/prompt_cache.py` | `.mcp.json` sha256 `514e6a5c8e16811835b75f8f260ac7467b9aac7c75cc7a9bf91811fef835d91c`, identical to the vendored seed; pin `code-indexing.mcp` `~0.1`; `CODEIDX_TOOLS` = the five published shapes; flag `AO_MCP_CODEIDX_ENABLED` default OFF |
| The consumed-contract row's honest state | `engine/memory/prompt_cache.py`, `CONSUMED_CONTRACTS` | `codeidx.context-pack/v1` → `state: "UNVERIFIED"`, naming vendor `#128` — row `C-13` |
| The institutional catalogue | `governance/knowledge/catalog.json` | 200 items across 7 kinds (`adr`, `architecture`, `golden-rules`, `governance`, `issue-metadata`, `pattern-template`, `policy`) — row `C-26` |
| This repo's own completion signals | `origin/master` history | `e6250d3` (#474), `7266ea5` (#475), `aaf8b4d` (#476), `5ddb30d` (#477), `6bc1463` (#481) |

## What this register does not claim

- **No capability invented on the vendor's behalf.** Every row is a live issue on the vendor board, or
  our own artifact. Nothing here proposes a feature the vendor has not been asked for.
- **No `shipped` claim without an artifact.** `C-01`–`C-09` each name the commit, table, release or
  file that proves the capability; a closed issue alone would not have been enough.
- **No claim that the index decides anything** (ADR-0018 decision 1). The index reports facts;
  authority stays in this repo's ADRs and declared contracts.
- **No vendor code and no vendor edit.** Nothing under `kushin77/code-indexing` is touched from here;
  needs go out as direction issues (NG4/NG6).
- **No gate.** This document adds no check. It is written to a stated grammar so that the gate
  `scripts/check-codeidx-capability-register.sh` (issue #480) can enforce it without either side
  guessing at the other.

## Gate contract — what the register offers `scripts/check-codeidx-capability-register.sh` (#480)

So the enforcement lane does not have to reverse-engineer this document from prose:

- **Rows** are the body rows of the single table under [the register](#the-register): one line, seven
  `|`-separated cells in the order `capability`, `needed-for`, `owner`, `ref`, `status`, `acceptance`,
  `verify`. No cell contains a literal `|`.
- **Cell values are plain text.** The three enumerated cells carry no inline-code backticks, so
  comparing the `owner` cell to `kushin77/code-indexing` / `us` / `both`, and the `status` cell to
  `shipped` / `in-flight` / `gap` / `UNVERIFIED`, is a straightforward string comparison — rules 1 and
  5 need no normalization. Only `capability`, `ref` and `verify` cells contain inline code.
- **Rule 1 — owner:** the `owner` cell is one of `kushin77/code-indexing`, `us`, `both`.
- **Rule 2 — acceptance:** the `acceptance` cell is non-empty and reads as an observable (it is the
  cell a reviewer can falsify).
- **Rule 3 — ref:** the `ref` cell is the literal `GAP` or contains an issue **link**; the link is the
  cell's first token, and the completion-signal suffix (` · closed=… · signal=…`) follows it.
- **Rule 4 — shipped needs a completion signal:** a `status` of `shipped` requires the `ref` cell to
  carry **both** a `closed=` and a `signal=` clause. A closed vendor issue with no `signal=` is the
  violation this rule exists for.
- **Rule 5 — gap needs a filed direction:** a `status` of `gap` requires the `ref` to be a link (a
  filed direction issue), never `GAP` — the `GAP` literal is reserved for a gap we deliberately did
  not file.
- **Rule 6 — #472 coverage:** each capability listed in
  [the #472 mapping](#the-consumption-epics-capabilities-against-this-register) must appear as a row
  `capability` (or be carried by a named row), so the consumption EPIC cannot go uncovered.
- **A register with zero parsed rows is a violation, not a pass.** This grammar was implemented and
  mutation-tested while the register was written: deleting every row, or deleting the single row the
  #472 mapping names, is silent under a naive parser — an emptied file must therefore fail by name,
  the same way an unreadable contract is reported CANNOT-ASSESS rather than conformant.
- **The `verify` column is documentation, not a gate input.** Several rows verify against the live
  vendor board and therefore need network access; `make verify` is offline and deterministic by
  doctrine, so the gate checks that `verify` is a non-empty command and does not execute it. Running
  a row's `verify` is a reviewer's act, and each one is a command that can fail.

## Reconciliation — the register against the board it cites (`agent-orchestrator#479`)

This section is **generated** by `scripts/track-codeidx-capabilities.sh --emit-doc-section`;
run the tracker rather than editing it by hand. It is not the register: the register's own rows
are the table under [the register](#the-register), and this is that table's `ref` column
reconciled against a recorded board state — the two are read together and are never confused
for one another.

**Recorded board state:** `scripts/track-codeidx-capabilities.snapshot.tsv`, recorded 2026-09-14T15:10:22Z
across the boards the register cites. The tracker reconciles that recording **offline and
deterministically** — `--live` reads the boards through the GitHub API and is opt-in, so nothing
here, and nothing in `make verify`, needs the network.

Every `ref` is reconciled, on whichever board it lives — the vendor's, CMR's, or this
repository's own board. A `ref` this tracker skipped would be the staleness it exists to catch,
so the board breakdown is reported rather than assumed: **32 row(s), 32 ref(s)** — `code-indexing` 26, `CMR` 1, `agent-orchestrator` 5.

| Row | Declared | Referenced issue | Live | Reconciliation |
|---|---|---|---|---|
| `C-01` | `shipped` | `kushin77/code-indexing#55` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-02` | `shipped` | `kushin77/code-indexing#5` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-03` | `shipped` | `kushin77/code-indexing#46` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-04` | `shipped` | `kushin77/code-indexing#80` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-05` | `shipped` | `kushin77/code-indexing#30` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-06` | `shipped` | `kushin77/code-indexing#19` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-07` | `shipped` | `kushin77/code-indexing#27` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-08` | `shipped` | `kushin77/code-indexing#73` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-09` | `shipped` | `kushin77/code-indexing#59` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-10` | `gap` | `kushin77/code-indexing#161` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-11` | `gap` | `kushin77/code-indexing#162` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-12` | `gap` | `kushin77/code-indexing#163` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-13` | `UNVERIFIED` | `kushin77/code-indexing#128` | `open` | `ok` — UNVERIFIED, and the provider's issue is still open |
| `C-14` | `gap` | `kushin77/code-indexing#106` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-15` | `gap` | `kushin77/code-indexing#104` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-16` | `gap` | `kushin77/code-indexing#107` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-17` | `gap` | `kushin77/code-indexing#108` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-18` | `gap` | `kushin77/code-indexing#126` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-19` | `gap` | `kushin77/code-indexing#111` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-20` | `in-flight` | `kushin77/code-indexing#132` | `open` | `ok` — in-flight, and the issue it cites is still open |
| `C-21` | `in-flight` | `kushin77/code-indexing#119` | `open` | `ok` — in-flight, and the issue it cites is still open |
| `C-22` | `gap` | `kushin77/code-indexing#122` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-23` | `gap` | `kushin77/code-indexing#147` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-24` | `gap` | `kushin77/code-indexing#134` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-25` | `gap` | `kushin77/CMR#1010` | `open` | `ok` — gap, and the issue it cites is still open |
| `C-26` | `shipped` | `kushin77/agent-orchestrator#474` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-27` | `shipped` | `kushin77/agent-orchestrator#475` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-28` | `shipped` | `kushin77/agent-orchestrator#476` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-29` | `in-flight` | `kushin77/code-indexing#129` | `open` | `ok` — in-flight, and the issue it cites is still open |
| `C-30` | `shipped` | `kushin77/agent-orchestrator#477` | `closed` | `ok` — shipped, closed, with a cited completion signal |
| `C-31` | `in-flight` | `kushin77/code-indexing#130` | `open` | `ok` — in-flight, and the issue it cites is still open |
| `C-32` | `shipped` | `kushin77/agent-orchestrator#481` | `closed` | `ok` — shipped, closed, with a cited completion signal |

**Totals:** 32 row(s), 32 matching, 0 mismatching (0 of them `UNVERIFIED`). A mismatch is exit 1 and every one is named above; a row
whose `ref` cannot be read at all is exit 2 and is never reported as `ok` — the tracker fails
closed.

### The mismatch classes this tracker names

| Class | The disagreement | What it means for the register |
|---|---|---|
| `SHIPPED-BUT-OPEN` | a row declares `shipped` and the issue it cites is **open** | the completion claim is ahead of the board: either the issue is open and the row overstates, or the row's `ref` is the wrong issue |
| `SHIPPED-NO-SIGNAL` | a row declares `shipped`, its issue is closed, and the row cites no `signal=` | a closed issue is not a completion signal, so the row is misclassified and belongs in `UNVERIFIED` |
| `UNVERIFIED` | a row declares `in-flight`/`gap`, its issue is closed, and no `signal=` is cited | the direction's outcome is unknown from here: the row can no longer be called a gap and cannot be called shipped either |
| `STALE-DECLARED` | a row declares `in-flight`/`gap`, its issue is closed, and a `signal=` **is** cited | the direction landed: the row is stale and must be re-grounded against the cited signal |
| `UNVERIFIED-BUT-CLOSED` | a row declares `UNVERIFIED` and its issue is closed | the provider may have published the contract after all, so the row's honest state must be re-checked |
| `REF-NOT-AN-ISSUE` | the `ref` resolves to a pull request | the register's `ref` grammar requires an issue link, and a board numbers issues and pull requests in one sequence, so a pull request is not a row's grounding |
