# Git templates & ecosystem gap analysis — class / pattern / template / env / governance / clobber / RCA / orphan-checker

Issue: [#608](https://github.com/kushin77/agent-orchestrator/issues/608) ·
Measured: **2026-09-14** · Lane: docs-only + board-only ·
Epic: [#616](https://github.com/kushin77/agent-orchestrator/issues/616)

## Why this doc exists

Issue #608 orders a gap analysis across **all of the git ecosystem**: class,
pattern, template, env variable, governance, clobber prevention, RCA, orphan
checking — plus anything missed — so the system runs faster, smarter, more
accurate and consistent, using the indexer, and is broken out into a proper
enterprise epic. This doc is that analysis. Every verdict is measured against
this repository's tree (2026-09-14, commit `6a2edd7`), cites the file path that
decides it, and maps each gap to exactly one remediation lane (one issue = one
file-surface, per `AGENTS.md` golden rule 2). The epic
([#616](https://github.com/kushin77/agent-orchestrator/issues/616)) is created;
its children are the remediation lanes.

## What counts as "the git ecosystem" here

The surfaces a git workflow actually touches in this repository: issue forms,
PR template, commit template, git config, hooks, ignore/attribute files, the
governance packages that mint lanes and claims (`dispatch`, `isolation`,
`lifecycle`, `reconcile`), the lessons/RCA ledger, the ADR process, the docs
index, and the knowledge indexer.

## Measured surface inventory

| Surface | Path (measured) | Enforcement (measured) | Verdict |
|---|---|---|---|
| Issue form (single) | `.github/ISSUE_TEMPLATE/fleet-task.yml` | `scripts/check-issue-template.sh` (issue #165, with negative control) | **PARTIAL** — one form only; no epic/bug forms |
| PR template | `.github/PULL_REQUEST_TEMPLATE.md` | `scripts/check-pr-contract.sh` (issue #288) | **OK** |
| Commit template | `.gitmessage` (issue #5) | **none** — `git config commit.template` is unset (measured rc 1) | **GAP** |
| Pre-commit config | `.pre-commit-config.yaml` | client-optional tooling; `make verify` runs its own mechanical secret scan | **PARTIAL** |
| Hooks | `git rev-parse --git-path hooks` | only `*.sample` files — no active hook (GR-15: no Actions; hooks stay client-side) | **PARTIAL** |
| Attributes | `.gitattributes` | **missing** (measured) | **GAP** |
| Ignore file | `.gitignore` (tracked) | covers `.research/`, `__pycache__`, tfstate, `.verify/`, `.board/locks/` — but coverage is not gated | **PARTIAL** |
| Surface classes | `docs/SURFACE-CLASS.md` + `make surface-class` | 7 declared surfaces; **no git-ecosystem row** | **GAP** |
| Pattern doctrine | none | hub `kushin77/CMR` `docs/SHELL-PATTERNS.md` exists; this repo has none | **GAP** |
| Env contract | `governance/isolation/` exports `AO_SESSION_ID` … `GIT_AUTHOR_*`/`GIT_COMMITTER_*` | named in `AGENTS.md` rule 15; no registry doc | **PARTIAL** |
| Governance spine | [`AGENTS.md`](../AGENTS.md), [`GOLDEN-RULES.md`](GOLDEN-RULES.md), [`GOVERNANCE.md`](GOVERNANCE.md) | claim/order gates (`check-chronological-dispatch.sh`, `check-issue-claims.sh`) | **OK** |
| RCA | [`governance/lessons/rca-template.md`](../governance/lessons/rca-template.md) + `ledger.jsonl` | `scripts/check-lessons.sh`, re-run by the board gate (issue #141) | **OK** |
| Orphan checker | [`governance/reconcile/README.md`](../governance/reconcile/README.md) | `scripts/check-reconcile.sh` (issue #304, rule 17) | **PARTIAL** — session-scoped |
| Knowledge index | [`governance/knowledge/sources.py`](../governance/knowledge/sources.py) | `scripts/check-knowledge-index.sh` | **PARTIAL** — 0 git-template sources registered (measured) |
| Docs index | [`docs/README.md`](README.md) | `scripts/check-docs.sh` checks links/whitespace, **not membership** | **GAP** |
| ADR process | [`decision-records/template.md`](decision-records/template.md) + index with reserved numbers | review-gated lifecycle | **OK** |

## Checklist findings

### 1. Class — GAP

[`SURFACE-CLASS.md`](SURFACE-CLASS.md) holds every product surface to its
measured rung of the CMR ladder (`template → class → pattern → enterprise →
faang → elite`). Measured today: seven declared surfaces (`shell`, `portal`,
`gateway`, `telemetry`, `registry`, `module-registry`, `module-brief`; scope
widened by #590). **None of the git-ecosystem surfaces is a declared row**, so
the gate never looks at `.github/**`, `.gitmessage`, or the git-governance
packages — the exact silent-scope failure #590 already recorded once.
Remediation lane: **#620** (`docs/SURFACE-CLASS.md`).

### 2. Pattern — GAP

The CMR hub carries a canonical, gate-enforced shell-pattern doctrine
(`kushin77/CMR` `docs/SHELL-PATTERNS.md`). This repo has **no pattern doctrine**
(measured: no `docs/SHELL-PATTERNS.md`, no `check-shell-patterns` gate), so the
50+ gate scripts follow patterns by convention only. Remediation lane: **#621**
(doc + enforcing gate in one lane, following the `FLEET-TEMPLATE.md`-plus-gate
repo pattern).

### 3. Template — PARTIAL

- `.github/ISSUE_TEMPLATE/` holds exactly **one** form, `fleet-task.yml`,
  and its gate is real (negative control included, vocabularies canonical).
  Missing: an epic form (objective / children / verification contract) and a
  bug form (observed vs expected / reproduction / RCA link). Lane **#622**.
- `.github/PULL_REQUEST_TEMPLATE.md` carries the merge contract
  `check-pr-contract.sh` parses (measured headings intact). Missing: the
  epic-edge note (`Parent: #<epic>`). Lane **#623**.
- `.gitmessage` exists (issue #5) but **`commit.template` is unset**
  (measured: `git config --get commit.template` → rc 1), so the template is
  advisory and nothing notices drift. Lanes **#623** (content) and **#624**
  (bootstrap gate that fails while unset).

### 4. Env variable — PARTIAL

The session env contract is real and mechanical: `governance/isolation/` mints
the session identity and exports `AO_SESSION_ID`, `AO_ISSUE`, `AO_BRANCH`,
`AO_WORKTREE`, `GIT_AUTHOR_*`, `GIT_COMMITTER_*` (measured: exporter in
`governance/isolation/`, named in `AGENTS.md` golden rule 15). What is missing
is a **canonical registry** a lane or checker can read — today the contract
lives only in prose. Lane **#627** (`docs/GIT-ENV-VARIABLES.md`).

### 5. Governance — OK (inventory doc was the gap)

`AGENTS.md` (canonical), `docs/GOLDEN-RULES.md` (rule format with per-rule
`Verify`), `docs/GOVERNANCE.md`, and the claim/order gates are all shipped and
wired. The one missing artifact was a single inventory of the git-ecosystem
surfaces — **this doc**, indexed in [`docs/README.md`](README.md) by the same
change.

### 6. Clobber prevention — PARTIAL

Strong and measured: tracked `.gitignore` covers research scratch, python
cache, Terraform state, `.verify/`, `.board/locks/`; lane isolation (golden
rule 2) keeps two lanes off one file; claim locks use `O_CREAT|O_EXCL`;
heartbeats and claim records are atomic/one-per-file so a writer never clobbers
another. Two measured holes: (a) nothing fails when a future lane adds a
runtime-state directory and forgets to ignore it — coverage is convention, not
gate — lane **#625** (`scripts/check-gitignore.sh`); (b) no `.gitattributes`,
so line endings and generated-file marking are per-editor convention — lane
**#624**.

### 7. RCA — OK

`governance/lessons/` ships the template (`rca-template.md`, issue #141), the
ledger, and the gate (`check-lessons.sh`), and the board gate re-runs it; an
artifact omitting a required section fails by name. No new lane is needed. One
residual, carried here rather than as a lane: the RCA template's "Detection"
section asks what *should* have caught the failure — for git-ecosystem
incidents the answer should name the gate from this analysis's recommendation
table.

### 8. Orphan checker — PARTIAL

`governance/reconcile/` ships heartbeats, sweep/watch, and the three-way
teardown (`reclaimed`/`parked`/`shelved`) with the never-trade-unmerged-work
invariant, gated by `check-reconcile.sh`. Measured gap: the audit is
**session-scoped** — a worktree or `issue-*` branch that no session beat, claim
record, or landed history explains is never reported. Lane **#628**.

## Anything I missed (measured, not guessed)

1. **`.gitattributes` is missing entirely** (measured) — line-ending
   normalization and `linguist-generated` marking for `.verify/**`/`*.pyc` are
   unenforced. Lane **#624**.
2. **Hooks are sample-only** (measured: `git rev-parse --git-path hooks` lists
   `*.sample` only). GR-15 forbids GitHub Actions; hooks cannot be
   fleet-enforced, so the honest posture is the bootstrap gate in lane **#624**
   plus the existing mechanical secret scan in `make verify` — not hooks.
3. **ADR numbering is manual but indexed** — `decision-records/README.md`
   reserves numbers; template + lifecycle are enforced. OK, no lane.
4. **Docs index completeness is ungated** — `check-docs.sh` resolves links and
   checks whitespace but a new `docs/*.md` that is never indexed in
   `docs/README.md` is silently invisible. Lane **#629**.
5. **Templates are invisible to the indexer** — `governance/knowledge/sources.py`
   is the single registration point, yet 0 git-template sources are registered
   (measured). Lane **#626**.
6. **New gate scripts are inert until wired** — `scripts/verify.sh` iterates an
   explicit `checks=(...)` array (not auto-discovered), and
   `scripts/gate-coverage-baseline.txt` must admit each new gate. The wiring is
   a dedicated lane, **#630**, deliberately `Blocked-by` the gate-script lanes
   so the wired list never names a gate that does not exist.

## Recommendation table — gap → remediation lane

| # | Gap | Lane | Owned files | Check that will enforce it |
|---|---|---|---|---|
| 1 | No surface-class rows for the git ecosystem | **#620** | `docs/SURFACE-CLASS.md` | `make surface-class` |
| 2 | No pattern doctrine or pattern gate | **#621** | `docs/SHELL-PATTERNS.md`, `scripts/check-shell-patterns.sh` (new) | `bash scripts/check-shell-patterns.sh` (wired by #630) |
| 3 | One issue form only | **#622** | `.github/ISSUE_TEMPLATE/**`, `scripts/check-issue-template.sh` | `bash scripts/check-issue-template.sh` |
| 4 | `.gitmessage` unenforced; PR template lacks epic edge | **#623** | `.gitmessage`, `.github/PULL_REQUEST_TEMPLATE.md` | `bash scripts/check-pr-contract.sh` |
| 5 | `commit.template` unset; no `.gitattributes` | **#624** | `.gitattributes` (new), `scripts/check-git-config.sh` (new) | `bash scripts/check-git-config.sh` (wired by #630) |
| 6 | Runtime-state dirs ignored by convention only | **#625** | `scripts/check-gitignore.sh` (new) | `bash scripts/check-gitignore.sh` (wired by #630) |
| 7 | Templates invisible to the indexer | **#626** | `governance/knowledge/sources.py` | `make knowledge-index` |
| 8 | Env contract lives in prose only | **#627** | `docs/GIT-ENV-VARIABLES.md` (new) | `bash scripts/check-docs.sh` |
| 9 | Orphan audit is session-scoped | **#628** | `governance/reconcile/**`, `scripts/check-reconcile.sh` | `bash scripts/check-reconcile.sh` |
| 10 | Docs index membership ungated | **#629** | `scripts/check-docs.sh` | `bash scripts/check-docs.sh` |
| 11 | New gates inert without wiring | **#630** | `scripts/verify.sh`, `scripts/gate-coverage-baseline.txt`, `Makefile` | `make verify` |

No two lanes share a file; the one ordering edge is the wiring lane
`Blocked-by` #621, #624, #625.

## Epic breakout (enterprise e2e)

Epic **#616** — "EPIC: Git-ecosystem templates & governance — class/pattern/
template/env/clobber/RCA/orphan gaps (issue #608)" — `type:epic`,
`class:enterprise`, `pillar:governance`, `priority:P2`. Children **#620–#630**
(above), each `type:task`, `class:enterprise`, `pillar:governance`,
`priority:P2`, each carrying `Parent: #616` in the body so the dispatch chain
resolves them, each with acceptance criteria and a `Verify:` command. The epic
closes only when every declared child is closed with evidence.

## Purpose — faster / smarter / more accurate / consistent, via the indexer

- **Faster:** templates + a canonical env registry mean a new lane starts
  without rediscovering contracts; the knowledge index answers "where is this
  artefact, is it current" in one query once lane **#626** registers them.
- **Smarter:** class and pattern declarations (lanes #620, #621) let the
  dispatcher hold each git surface to its measured rung instead of guessing.
- **More accurate:** every recommendation lands with a gate whose negative
  control must fail (AO-GR-4) — no declaration without enforcement.
- **Consistent:** one form contract, one commit contract, one env contract,
  all re-derived from the same docs the gates read.

## Verification contract of this analysis

This lane is docs-only + board-only: `docs/GIT-TEMPLATES-GAP-ANALYSIS.md` and
one index row in `docs/README.md` are the only tree changes. Evidence:
`make verify` green in the `issue-608` worktree, with the attestation naming
the commit (quoted in the PR that carries this doc).
